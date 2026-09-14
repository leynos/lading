"""Serialize and attribute compiler-cache measurements for publication.

The publish pipeline owns command sequencing through
:mod:`lading.commands.publish_sccache`. This module deliberately has no
runner, environment, or logging dependency: it renders counter summaries,
attributes snapshots to cargo invocations, and serializes the resulting
report. Keeping those pure bookkeeping concerns apart lets the session module
remain responsible solely for the sccache query lifecycle.
"""

from __future__ import annotations

import dataclasses as dc
import tempfile
from pathlib import Path

from lading.commands.publish_sccache_stats import (
    SccacheCounters,
    SccacheCrateRecord,
    SccacheSnapshot,
)


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


def write_atomically(path: Path, content: str) -> None:
    """Write ``content`` beside ``path`` and atomically replace it.

    Parameters
    ----------
    path : Path
        Destination path to replace after the temporary file has been written.
    content : str
        Text to write using UTF-8 encoding.

    Raises
    ------
    OSError
        If the temporary file cannot be written, closed, or replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if temporary is None:
            message = "Temporary file did not provide a filesystem path"
            raise OSError(message)
        temporary.replace(path)
    finally:
        if temporary is not None:
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
        """Counters accumulated since the baseline.

        Returns
        -------
        SccacheCounters
            The difference between the most recent snapshot and ``baseline``.
        """
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
