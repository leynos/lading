"""The outcomes of ``--expect-digests`` archive checks, and who is told of them.

An :class:`ArchiveOutcome` records what lading established about one crate's
archive. :class:`ArchiveObserver` is the small port through which the verifier
announces each decision, so the verifier itself neither logs nor counts;
:class:`LoggingObserver` is the production observer, which writes the log line
and increments the ``publish.archive_check`` counter. Rendering outcomes for the
publish summary is presentation, and lives in
:mod:`lading.commands.publish_digest_run`.

Examples
--------
>>> ArchiveOutcome.VERIFIED.value
'verified'
>>> ArchiveOutcome.CONFLICT in FAILURES
True
>>> ArchiveOutcome.UNAVAILABLE in FAILURES
True
"""

import dataclasses as dc
import enum
import logging
import typing as typ

from lading.utils import metrics

LOGGER = logging.getLogger(__name__)

#: Counter incremented once per crate outcome, labelled by ``outcome``.
ARCHIVE_CHECK_METRIC = "publish.archive_check"


class ArchiveOutcome(enum.StrEnum):
    """What lading established about one crate's archive."""

    VERIFIED = "verified"
    ALREADY_PUBLISHED = "already published and identical"
    CONFLICT = "conflict"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


FAILURES = frozenset({
    ArchiveOutcome.CONFLICT,
    ArchiveOutcome.MISMATCH,
    ArchiveOutcome.UNAVAILABLE,
})


@dc.dataclass(frozen=True, slots=True)
class CrateOutcome:
    """The outcome for one crate, with the reason it was reached.

    Attributes
    ----------
    crate : str
        The crate's name.
    version : str
        The crate's version.
    outcome : ArchiveOutcome
        What was established.
    detail : str
        A short human-readable reason.
    """

    crate: str
    version: str
    outcome: ArchiveOutcome
    detail: str


class ArchiveObserver(typ.Protocol):
    """Port told of each outcome as the verifier decides it."""

    def outcome_decided(self, decided: CrateOutcome) -> None:
        """Handle one decided outcome."""
        ...


class LoggingObserver:
    """Log each outcome and count it under :data:`ARCHIVE_CHECK_METRIC`.

    Examples
    --------
    >>> LoggingObserver().outcome_decided(
    ...     CrateOutcome("alpha", "0.1.0", ArchiveOutcome.VERIFIED, "matches")
    ... )
    """

    def outcome_decided(self, decided: CrateOutcome) -> None:
        """Increment the counter and log ``decided`` (at ERROR for a failure)."""
        metrics.increment_counter(
            ARCHIVE_CHECK_METRIC, outcome=decided.outcome.name.lower()
        )
        log = LOGGER.error if decided.outcome in FAILURES else LOGGER.info
        log(
            "Archive check for crate %s %s: %s (%s)",
            decided.crate,
            decided.version,
            decided.outcome.value,
            decided.detail,
        )
