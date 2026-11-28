"""Count and log the crates.io archive lookups ``--expect-digests`` makes.

:func:`~lading.commands.publish_registry.fetch_crates_io_digest` reports each
attempt's result through one :class:`FetchTelemetry`: a retry is logged with
the attempt number, the elapsed time and the budget left, and a lookup that
gives up logs one bounded summary (attempts, elapsed time and a failure
category) before its error propagates. Every attempt increments
:data:`ARCHIVE_FETCH_METRIC` once, labelled only with a fixed
:class:`FetchResult`, so the counter never carries a URL, a crate name or error
text.

Examples
--------
>>> FetchResult.NOT_PUBLISHED.value
'not_published'
>>> failure_category(OSError("connection reset"))
'network'
"""

import collections.abc as cabc
import dataclasses as dc
import enum
import http.client
import logging
import urllib.error

from lading.commands.publish_registry_reads import BudgetExhaustedError
from lading.utils import metrics

LOGGER = logging.getLogger("lading.commands.publish_registry")

#: Counter incremented once per lookup attempt, labelled by ``result``.
ARCHIVE_FETCH_METRIC = "publish.archive_fetch"

_MILLISECONDS = 1000


class FetchResult(enum.StrEnum):
    """How one attempt to fetch a published archive ended."""

    SUCCEEDED = "succeeded"
    NOT_PUBLISHED = "not_published"
    RETRIED = "retried"
    UNAVAILABLE = "unavailable"


def failure_category(cause: BaseException | None) -> str:
    """Return a fixed name for why a lookup gave up, from its cause.

    Returns
    -------
    str
        One of ``budget_exhausted``, ``http_status``, ``timeout``,
        ``incomplete_body``, ``network``, ``oversized_response`` (a lookup
        refused without a transport cause) or ``other``.
    """
    match cause:
        case BudgetExhaustedError():
            category = "budget_exhausted"
        case urllib.error.HTTPError():
            category = "http_status"
        case TimeoutError():
            category = "timeout"
        case http.client.IncompleteRead():
            category = "incomplete_body"
        case OSError():
            category = "network"
        case None:
            category = "oversized_response"
        case _:
            category = "other"
    return category


@dc.dataclass(slots=True)
class FetchTelemetry:
    """The counter and log lines of one archive lookup.

    Attributes
    ----------
    url : str
        The archive being fetched, named in the log lines only.
    clock : Callable[[], float]
        The lookup's monotonic clock.
    max_attempts : int
        How many attempts the lookup may make.
    """

    url: str
    clock: cabc.Callable[[], float]
    max_attempts: int
    attempts: int = dc.field(default=0, init=False)
    _started_at: float = dc.field(default=0.0, init=False)

    def __post_init__(self) -> None:
        """Start timing the lookup."""
        self._started_at = self.clock()

    def attempt_started(self) -> None:
        """Note that another attempt has begun."""
        self.attempts += 1

    def _elapsed_ms(self) -> int:
        """Return the milliseconds since the lookup began."""
        return round((self.clock() - self._started_at) * _MILLISECONDS)

    def retrying(self, error: BaseException, remaining_seconds: float) -> None:
        """Count a failed attempt that will be retried, and log it."""
        metrics.increment_counter(
            ARCHIVE_FETCH_METRIC, result=FetchResult.RETRIED.value
        )
        LOGGER.warning(
            "Fetching %s failed on attempt %d of %d after %d ms (%s); "
            "retrying with %d ms of the time budget left",
            self.url,
            self.attempts,
            self.max_attempts,
            self._elapsed_ms(),
            error,
            round(remaining_seconds * _MILLISECONDS),
        )

    def finished(self, *, is_published: bool) -> None:
        """Count an attempt that settled the lookup."""
        result = FetchResult.SUCCEEDED if is_published else FetchResult.NOT_PUBLISHED
        metrics.increment_counter(ARCHIVE_FETCH_METRIC, result=result.value)

    def gave_up(self, cause: BaseException | None) -> None:
        """Count the final failed attempt and log the lookup's summary."""
        metrics.increment_counter(
            ARCHIVE_FETCH_METRIC, result=FetchResult.UNAVAILABLE.value
        )
        LOGGER.error(
            "Giving up on %s after %d attempt(s) and %d ms (%s)",
            self.url,
            self.attempts,
            self._elapsed_ms(),
            failure_category(cause),
        )
