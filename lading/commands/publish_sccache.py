"""Opt-in sccache statistics around the publish pipeline's cargo builds.

``cargo package --verify`` and ``cargo publish --dry-run`` compile each crate
from a packaged copy whose paths and manifest differ from the workspace build,
so whether those compilation units hit the compiler cache is a real question
that a job-wide ``sccache --show-stats`` cannot answer (issue #252). When
``lading publish --sccache-stats`` is given, this module queries the sccache
binary named by ``RUSTC_WRAPPER`` for a baseline snapshot before the first
cargo build and again after every per-crate cargo invocation, differences the
snapshots, and logs one bounded line per invocation. An optional JSON report keeps
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

    Parameters
    ----------
    counters : SccacheCounters
        The counters to render.

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

    Parameters
    ----------
    record : SccacheCrateRecord
        The invocation's crate, subcommand, elapsed seconds, and counters.

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
class SccacheLedger:
    """Pure bookkeeping for one instrumented pipeline: no I/O, no logging.

    Holds the baseline snapshot, the most recent snapshot, and the records
    attributed so far. :meth:`attribute` is the reducer: it takes the next
    snapshot and the invocation it followed and returns the record for that
    invocation.

    Examples
    --------
    >>> zero = SccacheSnapshot({}, SccacheCounters())
    >>> ledger = SccacheLedger(baseline=zero)
    >>> later = SccacheSnapshot({}, SccacheCounters(requests=3, hits=2, misses=1))
    >>> record = ledger.attribute(
    ...     later, crate="alpha", subcommand="package", seconds=1.5
    ... )
    >>> record.counters
    SccacheCounters(requests=3, hits=2, misses=1, errors=0)
    >>> ledger.delta
    SccacheCounters(requests=3, hits=2, misses=1, errors=0)
    """

    baseline: SccacheSnapshot
    previous: SccacheSnapshot = dc.field(init=False)
    records: list[SccacheCrateRecord] = dc.field(default_factory=list)

    def __post_init__(self) -> None:
        """Start differencing from the baseline."""
        self.previous = self.baseline

    def attribute(
        self,
        snapshot: SccacheSnapshot,
        *,
        crate: str,
        subcommand: str,
        seconds: float,
    ) -> SccacheCrateRecord:
        """Record the counters between the previous snapshot and ``snapshot``.

        Parameters
        ----------
        snapshot : SccacheSnapshot
            The snapshot taken after the invocation.
        crate : str
            The crate the invocation ran for.
        subcommand : str
            ``package`` or ``publish``.
        seconds : float
            The invocation's elapsed time.

        Returns
        -------
        SccacheCrateRecord
            The counters attributed to the invocation, also appended to
            :attr:`records`.
        """
        record = SccacheCrateRecord(
            crate=crate,
            subcommand=subcommand,
            seconds=seconds,
            counters=snapshot.counters - self.previous.counters,
        )
        self.records.append(record)
        self.previous = snapshot
        return record

    @property
    def delta(self) -> SccacheCounters:
        """Counters accumulated since the baseline."""
        return self.previous.counters - self.baseline.counters

    def report(self, wrapper: Path) -> dict[str, object]:
        """Return the JSON-ready report for ``wrapper``.

        Parameters
        ----------
        wrapper : Path
            The sccache binary the snapshots came from, recorded verbatim.

        Returns
        -------
        dict[str, object]
            ``wrapper``, raw ``baseline`` and ``final`` payloads, one entry per
            invocation under ``crates``, and the pipeline ``delta``.
        """
        return {
            "wrapper": str(wrapper),
            "baseline": self.baseline.raw,
            "final": self.previous.raw,
            "crates": [record.as_dict() for record in self.records],
            "delta": self.delta.as_dict(),
        }


@dc.dataclass(slots=True)
class SccacheSession:
    """Sequence the sccache queries around one publish pipeline.

    The session owns the side effects (queries through the command runner,
    log lines, the report file) and delegates the arithmetic to a
    :class:`SccacheLedger`. Every failure is a WARNING that disables further
    queries; no method raises into the pipeline.

    Parameters
    ----------
    wrapper : Path
        The sccache binary named by ``RUSTC_WRAPPER``.
    runner : CommandRunner
        Command runner used for every query.
    cwd : Path
        Working directory for the queries (the workspace root).
    json_path : Path | None
        Where to write the JSON report (already resolved against the
        workspace root by :func:`create_session`), or :data:`None` to skip it.
    """

    wrapper: Path
    runner: CommandRunner
    cwd: Path
    json_path: Path | None = None
    enabled: bool = True
    _ledger: SccacheLedger | None = None

    @property
    def records(self) -> tuple[SccacheCrateRecord, ...]:
        """The per-invocation records collected so far."""
        return () if self._ledger is None else tuple(self._ledger.records)

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

    def _active_ledger(self) -> SccacheLedger | None:
        """Return the ledger while the session is live, else :data:`None`."""
        return self._ledger if self.enabled else None

    def begin(self) -> None:
        """Take the baseline snapshot before the first cargo build."""
        if not self.enabled:
            return
        LOGGER.info("Compiler cache statistics enabled via %s", self.wrapper)
        snapshot = self._snapshot("baseline")
        if snapshot is not None:
            self._ledger = SccacheLedger(baseline=snapshot)

    def record(self, crate: str, subcommand: str, seconds: float) -> None:
        """Attribute the counters since the previous snapshot to one invocation.

        Never raises: a failed query logs a WARNING and disables the session.

        Parameters
        ----------
        crate : str
            The crate the invocation ran for.
        subcommand : str
            ``package`` or ``publish``.
        seconds : float
            The invocation's elapsed time, reported on the summary line.
        """
        ledger = self._active_ledger()
        if ledger is None:
            return
        snapshot = self._snapshot(f"cargo {subcommand} {crate}")
        if snapshot is None:
            return
        record = ledger.attribute(
            snapshot, crate=crate, subcommand=subcommand, seconds=seconds
        )
        LOGGER.info("%s", format_crate_summary(record))

    def finish(self) -> None:
        """Log the pipeline delta, mirror ``--show-stats``, and write the report.

        Never raises: failures log a WARNING and leave the publish outcome
        untouched.
        """
        ledger = self._active_ledger()
        if ledger is None:
            return
        LOGGER.info(
            "Compiler cache over the publish pipeline: %s",
            format_counters(ledger.delta),
        )
        self._mirror_text_statistics()
        if self.json_path is not None:
            self._write_report(self.json_path, ledger.report(self.wrapper))

    def _mirror_text_statistics(self) -> None:
        """Log the human-readable statistics, labelled as server-cumulative."""
        try:
            text = query_text(self.wrapper, runner=self.runner, cwd=self.cwd)
        except SccacheStatsError as exc:
            metrics.increment_counter(QUERY_METRIC, outcome="failure")
            LOGGER.warning("Compiler cache statistics text unavailable: %s", exc)
            return
        metrics.increment_counter(QUERY_METRIC, outcome="success")
        LOGGER.info(
            "sccache statistics (cumulative for the server's lifetime):\n%s",
            text.rstrip(),
        )

    def _write_report(self, json_path: Path, report: dict[str, object]) -> None:
        """Write ``report`` to ``json_path`` atomically, warning on failure."""
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
        :data:`None` when neither ``sccache_stats`` nor ``sccache_stats_json``
        is set, or when instrumentation is requested but no sccache wrapper
        is configured (a WARNING is logged in that case).
    """
    # A report path implies the measurement, for CLI and library callers alike.
    if not (options.sccache_stats or options.sccache_stats_json is not None):
        return None
    wrapper = detect_wrapper(os.environ if env is None else env)
    if wrapper is None:
        LOGGER.warning(
            "Compiler cache statistics requested but %s does not name an sccache "
            "binary; skipping",
            RUSTC_WRAPPER_ENV_VAR,
        )
        return None
    json_path = options.sccache_stats_json
    if json_path is not None:
        # Relative report paths follow the workspace root, like every other
        # path lading accepts; joining leaves an absolute path unchanged.
        json_path = workspace_root / json_path
    return SccacheSession(
        wrapper=wrapper,
        runner=runner,
        cwd=workspace_root,
        json_path=json_path,
    )


__all__ = [
    "QUERY_METRIC",
    "SccacheLedger",
    "SccacheSession",
    "create_session",
    "format_counters",
    "format_crate_summary",
]
