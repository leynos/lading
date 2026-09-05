"""Opt-in sccache statistics around the publish pipeline's cargo builds.

``cargo package --verify`` and ``cargo publish --dry-run`` compile each crate
from a packaged copy whose paths and manifest differ from the workspace build,
so whether those compilation units hit the compiler cache is a real question
that a job-wide ``sccache --show-stats`` cannot answer (issue #252). When
``lading publish --sccache-stats`` is given, this module queries the sccache
binary named by ``RUSTC_WRAPPER`` for a baseline snapshot before the first
cargo build and again after every per-crate cargo invocation, differences the
snapshots, and logs one bounded line per crate. An optional JSON report keeps
the raw payloads for after-the-fact comparison.

Instrumentation must never fail a release or a release rehearsal: every
failure here is reported as a WARNING and disables further queries.

Related modules
---------------
* :mod:`lading.commands.publish` owns the pipeline and calls
  :func:`create_session`, :meth:`SccacheSession.begin`,
  :meth:`SccacheSession.record`, and :meth:`SccacheSession.finish`.
* :mod:`lading.commands.publish_execution` times each cargo invocation; the
  elapsed seconds it measures are what :meth:`SccacheSession.record` reports.
* :mod:`lading.commands.publish_sccache_stats` detects the wrapper, runs the
  ``--show-stats`` queries, and parses the counters.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import logging
import os
import tempfile
import typing as typ
from pathlib import Path

from lading.commands.publish_sccache_stats import (
    RUSTC_WRAPPER_ENV_VAR,
    SccacheCounters,
    SccacheCrateRecord,
    SccacheSnapshot,
    SccacheStatsError,
    detect_wrapper,
    query_snapshot,
    query_text,
)
from lading.utils import metrics

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

LOGGER = logging.getLogger(__name__)

# Metric names (issue #252); documented in docs/developers-guide.md.
QUERY_METRIC = "publish.sccache.query"


def format_counters(counters: SccacheCounters) -> str:
    """Render ``counters`` as the fixed ``requests= hits= misses= errors=`` tail.

    Returns
    -------
    str
        The four counters as ``key=value`` pairs separated by spaces.

    Examples
    --------
    >>> format_counters(SccacheCounters(requests=412, hits=398, misses=14))
    'requests=412 hits=398 misses=14 errors=0'
    """
    return (
        f"requests={counters.requests} hits={counters.hits} "
        f"misses={counters.misses} errors={counters.errors}"
    )


def format_crate_summary(record: SccacheCrateRecord) -> str:
    """Render the one-line per-crate summary.

    Returns
    -------
    str
        ``Compiler cache for cargo <subcommand> <crate>: <seconds>s, <counters>``.

    Examples
    --------
    >>> record = SccacheCrateRecord(
    ...     "rstest-bdd", "package", 84.2,
    ...     SccacheCounters(requests=412, hits=398, misses=14),
    ... )
    >>> format_crate_summary(record)  # doctest: +NORMALIZE_WHITESPACE
    'Compiler cache for cargo package rstest-bdd: 84.2s,
    requests=412 hits=398 misses=14 errors=0'
    """
    return (
        f"Compiler cache for cargo {record.subcommand} {record.crate}: "
        f"{record.seconds:.1f}s, {format_counters(record.counters)}"
    )


def _write_atomically(path: Path, content: str) -> None:
    """Write ``content`` beside ``path`` and atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, prefix=f".{path.name}."
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dc.dataclass(slots=True)
class SccacheSession:
    """Running state of one instrumented publish pipeline.

    Parameters
    ----------
    wrapper : Path
        The sccache binary named by ``RUSTC_WRAPPER``.
    runner : CommandRunner
        Command runner used for every query.
    cwd : Path
        Working directory for the queries (the workspace root).
    json_path : Path | None
        Where to write the JSON report, or :data:`None` to skip it.
    """

    wrapper: Path
    runner: CommandRunner
    cwd: Path
    json_path: Path | None = None
    enabled: bool = True
    _baseline: SccacheSnapshot | None = None
    _previous: SccacheSnapshot | None = None
    _records: list[SccacheCrateRecord] = dc.field(default_factory=list)

    @property
    def records(self) -> tuple[SccacheCrateRecord, ...]:
        """The per-crate records collected so far."""
        return tuple(self._records)

    def _snapshot(self, purpose: str) -> SccacheSnapshot | None:
        """Take a snapshot, or disable the session with a WARNING on failure."""
        try:
            snapshot = query_snapshot(self.wrapper, runner=self.runner, cwd=self.cwd)
        except SccacheStatsError as exc:
            metrics.increment_counter(QUERY_METRIC, outcome="failure")
            LOGGER.warning(
                "Compiler cache statistics unavailable (%s); disabling: %s",
                purpose,
                exc,
            )
            self.enabled = False
            return None
        metrics.increment_counter(QUERY_METRIC, outcome="success")
        return snapshot

    def begin(self) -> None:
        """Take the baseline snapshot before the first cargo build."""
        if not self.enabled:
            return
        LOGGER.info("Compiler cache statistics enabled via %s", self.wrapper)
        snapshot = self._snapshot("baseline")
        self._baseline = snapshot
        self._previous = snapshot

    def _active_snapshots(self) -> tuple[SccacheSnapshot, SccacheSnapshot] | None:
        """Return ``(baseline, previous)`` while the session is live, else None."""
        if not self.enabled:
            return None
        if self._baseline is None or self._previous is None:
            return None
        return self._baseline, self._previous

    def record(self, crate: str, subcommand: str, seconds: float) -> None:
        """Attribute the counters since the previous snapshot to one invocation.

        Never raises: a failed query logs a WARNING and disables the session.
        """
        active = self._active_snapshots()
        if active is None:
            return
        _baseline, previous = active
        snapshot = self._snapshot(f"cargo {subcommand} {crate}")
        if snapshot is None:
            return
        record = SccacheCrateRecord(
            crate=crate,
            subcommand=subcommand,
            seconds=seconds,
            counters=snapshot.counters - previous.counters,
        )
        self._records.append(record)
        self._previous = snapshot
        LOGGER.info(format_crate_summary(record))

    def finish(self) -> None:
        """Log the pipeline delta, mirror ``--show-stats``, and write the report.

        Never raises: failures log a WARNING and leave the publish outcome
        untouched.
        """
        active = self._active_snapshots()
        if active is None:
            return
        baseline, previous = active
        delta = previous.counters - baseline.counters
        LOGGER.info(
            "Compiler cache over the publish pipeline: %s", format_counters(delta)
        )
        self._mirror_text_statistics()
        if self.json_path is not None:
            self._write_report(self.json_path, baseline, previous, delta)

    def _mirror_text_statistics(self) -> None:
        """Log the human-readable statistics, labelled as server-cumulative."""
        try:
            text = query_text(self.wrapper, runner=self.runner, cwd=self.cwd)
        except SccacheStatsError as exc:
            LOGGER.warning("Compiler cache statistics text unavailable: %s", exc)
            return
        LOGGER.info(
            "sccache statistics (cumulative for the server's lifetime):\n%s",
            text.rstrip(),
        )

    def _write_report(
        self,
        json_path: Path,
        baseline: SccacheSnapshot,
        final: SccacheSnapshot,
        delta: SccacheCounters,
    ) -> None:
        """Write the JSON report to ``json_path``, warning on failure."""
        report = {
            "wrapper": str(self.wrapper),
            "baseline": baseline.raw,
            "final": final.raw,
            "crates": [record.as_dict() for record in self._records],
            "delta": delta.as_dict(),
        }
        try:
            _write_atomically(json_path, json.dumps(report, sort_keys=True))
        except OSError as exc:
            LOGGER.warning(
                "Could not write compiler cache report to %s: %s", json_path, exc
            )
            return
        LOGGER.info("Compiler cache report written to %s", json_path)


class _SccacheOptions(typ.Protocol):
    """The two publish options this module reads."""

    @property
    def sccache_stats(self) -> bool:
        """Whether compiler-cache statistics were requested."""

    @property
    def sccache_stats_json(self) -> Path | None:
        """Where the JSON report goes, or :data:`None` for no report."""


def create_session(
    options: _SccacheOptions,
    *,
    runner: CommandRunner,
    workspace_root: Path,
    env: cabc.Mapping[str, str] | None = None,
) -> SccacheSession | None:
    """Return a session when instrumentation is requested and possible.

    Parameters
    ----------
    options : _SccacheOptions
        Publish options carrying ``sccache_stats`` and ``sccache_stats_json``.
    runner : CommandRunner
        Command runner used for the sccache queries.
    workspace_root : Path
        Working directory for the queries.
    env : Mapping[str, str] | None
        Environment to inspect for ``RUSTC_WRAPPER``; defaults to
        :data:`os.environ`.

    Returns
    -------
    SccacheSession | None
        :data:`None` when instrumentation is off, or when it is on but no
        sccache wrapper is configured (a WARNING is logged in that case).
    """
    if not options.sccache_stats:
        return None
    wrapper = detect_wrapper(os.environ if env is None else env)
    if wrapper is None:
        LOGGER.warning(
            "Compiler cache statistics requested but %s does not name an sccache "
            "binary; skipping",
            RUSTC_WRAPPER_ENV_VAR,
        )
        return None
    return SccacheSession(
        wrapper=wrapper,
        runner=runner,
        cwd=workspace_root,
        json_path=options.sccache_stats_json,
    )


__all__ = [
    "QUERY_METRIC",
    "SccacheSession",
    "create_session",
    "format_counters",
    "format_crate_summary",
]
