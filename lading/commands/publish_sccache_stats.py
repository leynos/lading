"""Query and parse sccache statistics for the publish pipeline (issue #252).

This module is the adapter side of the opt-in compiler-cache instrumentation:
it finds the sccache binary that ``RUSTC_WRAPPER`` names, runs its
``--show-stats`` queries through the :class:`~lading.runtime.CommandRunner`
port, and reduces the JSON payload to the four counters a cache-usage question
needs. :mod:`lading.commands.publish_sccache` owns the session that sequences
those queries around the cargo builds and reports the results.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import typing as typ
from pathlib import Path, PureWindowsPath

from lading.exceptions import LadingError
from lading.runtime import coerce_text
from lading.utils.process import append_detail, command_detail

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

RUSTC_WRAPPER_ENV_VAR = "RUSTC_WRAPPER"
_WRAPPER_BASENAME_PREFIX = "sccache"

_SHOW_STATS_JSON_ARGS: tuple[str, ...] = ("--show-stats", "--stats-format=json")
_SHOW_STATS_TEXT_ARGS: tuple[str, ...] = ("--show-stats",)


class SccacheStatsError(LadingError):
    """Local root for failures while querying or parsing sccache statistics."""


class SccacheQueryError(SccacheStatsError):
    """Raised when the sccache binary exits non-zero or cannot be spawned."""

    def __init__(
        self, wrapper: Path, exit_code: int | None, stdout: str, stderr: str
    ) -> None:
        """Capture the failed invocation's exit status and streams."""
        self.wrapper = wrapper
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        status = "could not be run" if exit_code is None else f"exited {exit_code}"
        message = append_detail(
            f"{wrapper} --show-stats {status}", command_detail(stdout, stderr)
        )
        super().__init__(message)


class SccacheStatsParseError(SccacheStatsError):
    """Raised when sccache's JSON statistics cannot be parsed.

    ``reason`` is machine-readable: ``"invalid-json"`` when the output is not
    JSON at all, ``"non-object"`` when it parses to something other than an
    object.
    """

    def __init__(self, wrapper: Path, reason: str) -> None:
        """Capture the queried wrapper and the parse failure kind."""
        self.wrapper = wrapper
        self.reason = reason
        detail = {
            "invalid-json": "produced invalid JSON output",
            "non-object": "returned a non-object JSON payload",
        }.get(reason, reason)
        message = f"{wrapper} --show-stats {detail}"
        super().__init__(message)


@dc.dataclass(frozen=True, slots=True)
class SccacheCounters:
    """The four counters a cache-usage question needs, summed across languages."""

    requests: int = 0
    hits: int = 0
    misses: int = 0
    errors: int = 0

    def __sub__(self, other: SccacheCounters) -> SccacheCounters:
        """Return the counter-wise difference ``self - other``."""
        return SccacheCounters(
            requests=self.requests - other.requests,
            hits=self.hits - other.hits,
            misses=self.misses - other.misses,
            errors=self.errors - other.errors,
        )

    def as_dict(self) -> dict[str, int]:
        """Return the counters as a plain mapping for JSON output."""
        return dc.asdict(self)


@dc.dataclass(frozen=True, slots=True)
class SccacheSnapshot:
    """One ``--show-stats --stats-format=json`` payload and its parsed counters."""

    raw: dict[str, object]
    counters: SccacheCounters


@dc.dataclass(frozen=True, slots=True)
class SccacheCrateRecord:
    """Cache counters attributed to one per-crate cargo invocation."""

    crate: str
    subcommand: str
    seconds: float
    counters: SccacheCounters

    def as_dict(self) -> dict[str, object]:
        """Return the record flattened for the JSON report's ``crates`` list."""
        return {
            "crate": self.crate,
            "subcommand": self.subcommand,
            "seconds": round(self.seconds, 3),
            **self.counters.as_dict(),
        }


def detect_wrapper(env: cabc.Mapping[str, str]) -> Path | None:
    """Return the sccache binary named by ``RUSTC_WRAPPER``, if any.

    Parameters
    ----------
    env : Mapping[str, str]
        Environment to inspect; production passes :data:`os.environ`.

    Returns
    -------
    Path | None
        The wrapper path when its basename starts with ``sccache`` (so
        ``sccache`` and ``sccache.exe`` both qualify), otherwise :data:`None`.

    Examples
    --------
    >>> detect_wrapper({"RUSTC_WRAPPER": "/opt/bin/sccache"})
    PosixPath('/opt/bin/sccache')
    >>> detect_wrapper({"RUSTC_WRAPPER": "/opt/bin/ccache"}) is None
    True
    >>> detect_wrapper({}) is None
    True
    """
    value = env.get(RUSTC_WRAPPER_ENV_VAR, "").strip()
    if not value:
        return None
    # ``PureWindowsPath`` treats both separators as separators on every host,
    # so a Windows wrapper path is recognized on Linux; case-fold so
    # ``SCCACHE.EXE`` qualifies. The original path is kept for execution.
    basename = PureWindowsPath(value).name.casefold()
    if not basename.startswith(_WRAPPER_BASENAME_PREFIX):
        return None
    return Path(value)


def _sum_language_counts(section: object) -> int:
    """Return the sum of a ``{"counts": {lang: n}}`` section, tolerating gaps."""
    if not isinstance(section, dict):
        return 0
    counts = section.get("counts")
    if not isinstance(counts, dict):
        return 0
    return sum(int(value) for value in counts.values() if isinstance(value, int))


def _int_field(stats: cabc.Mapping[str, object], key: str) -> int:
    """Return ``stats[key]`` as an int, or zero when absent or not numeric."""
    value = stats.get(key, 0)
    return value if isinstance(value, int) else 0


def parse_counters(payload: cabc.Mapping[str, object]) -> SccacheCounters:
    """Extract :class:`SccacheCounters` from a ``--stats-format=json`` payload.

    The keys read (``compile_requests``, ``cache_hits``, ``cache_misses``,
    ``cache_errors``, ``cache_read_errors``, ``cache_write_errors``, and
    ``cache_timeouts`` under ``stats``) are present in sccache 0.12, 0.14,
    and 0.17.
    Missing or malformed keys count as zero rather than failing the run.

    Parameters
    ----------
    payload : Mapping[str, object]
        The decoded JSON document sccache printed.

    Returns
    -------
    SccacheCounters
        Requests, hits, misses, and the sum of every error counter.

    Examples
    --------
    >>> parse_counters({"stats": {"compile_requests": 3,
    ...     "cache_hits": {"counts": {"Rust": 2}},
    ...     "cache_misses": {"counts": {"Rust": 1}}}})
    SccacheCounters(requests=3, hits=2, misses=1, errors=0)
    """
    stats_section = payload.get("stats")
    if not isinstance(stats_section, dict):
        return SccacheCounters()
    stats = typ.cast("cabc.Mapping[str, object]", stats_section)
    errors = (
        _sum_language_counts(stats.get("cache_errors"))
        + _int_field(stats, "cache_read_errors")
        + _int_field(stats, "cache_write_errors")
        + _int_field(stats, "cache_timeouts")
    )
    return SccacheCounters(
        requests=_int_field(stats, "compile_requests"),
        hits=_sum_language_counts(stats.get("cache_hits")),
        misses=_sum_language_counts(stats.get("cache_misses")),
        errors=errors,
    )


def _query(
    wrapper: Path,
    arguments: tuple[str, ...],
    *,
    runner: CommandRunner,
    cwd: Path,
) -> str:
    """Run ``wrapper`` with ``arguments`` and return its stdout text."""
    try:
        exit_code, stdout, stderr = runner(
            (str(wrapper), *arguments), cwd=cwd, echo_stdout=False
        )
    except LadingError as exc:
        raise SccacheQueryError(wrapper, None, "", str(exc)) from exc
    stdout_text = coerce_text(stdout)
    if exit_code != 0:
        raise SccacheQueryError(wrapper, exit_code, stdout_text, coerce_text(stderr))
    return stdout_text


def query_snapshot(
    wrapper: Path, *, runner: CommandRunner, cwd: Path
) -> SccacheSnapshot:
    """Query ``wrapper --show-stats --stats-format=json`` and parse the result.

    Parameters
    ----------
    wrapper : Path
        The sccache binary to query; the same one cargo invokes.
    runner : CommandRunner
        Command runner used to execute the query.
    cwd : Path
        Working directory for the query.

    Returns
    -------
    SccacheSnapshot
        The raw payload and its parsed counters.

    Raises
    ------
    SccacheQueryError
        If the binary cannot be run or exits non-zero (propagated from
        :func:`_query`).
    SccacheStatsParseError
        If the output is not a JSON object.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by _query
    stdout = _query(wrapper, _SHOW_STATS_JSON_ARGS, runner=runner, cwd=cwd)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SccacheStatsParseError(wrapper, "invalid-json") from exc
    if not isinstance(payload, dict):
        raise SccacheStatsParseError(wrapper, "non-object")
    return SccacheSnapshot(raw=payload, counters=parse_counters(payload))


def query_text(wrapper: Path, *, runner: CommandRunner, cwd: Path) -> str:
    """Return the human-readable ``wrapper --show-stats`` output.

    Parameters
    ----------
    wrapper : Path
        The sccache binary to query.
    runner : CommandRunner
        Command runner used to execute the query.
    cwd : Path
        Working directory for the query.

    Returns
    -------
    str
        The text sccache printed, for mirroring into the log.

    Raises
    ------
    SccacheQueryError
        If the binary cannot be run or exits non-zero (propagated from
        :func:`_query`).
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by _query
    return _query(wrapper, _SHOW_STATS_TEXT_ARGS, runner=runner, cwd=cwd)


__all__ = [
    "RUSTC_WRAPPER_ENV_VAR",
    "SccacheCounters",
    "SccacheCrateRecord",
    "SccacheQueryError",
    "SccacheSnapshot",
    "SccacheStatsError",
    "SccacheStatsParseError",
    "detect_wrapper",
    "parse_counters",
    "query_snapshot",
    "query_text",
]
