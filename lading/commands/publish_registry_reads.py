"""Read a crates.io archive response within the lookup's time budget.

:mod:`lading.commands.publish_registry` downloads a published archive to hash
it. This module owns the time side of that download: the :class:`Deadline` one
lookup shares across its attempts, and :func:`read_chunk`, which reads the body
one bounded chunk at a time so that no single blocking read waits past the
budget's remaining time.

Examples
--------
>>> deadline = Deadline(clock=lambda: 10.0, expires_at=15.0, budget_seconds=180.0)
>>> deadline.remaining()
5.0
"""

import collections.abc as cabc
import dataclasses as dc
import typing as typ

REQUEST_TIMEOUT_SECONDS = 60.0
_READ_CHUNK_BYTES = 1024 * 1024


class Response(typ.Protocol):
    """The slice of an HTTP response the fetcher reads."""

    def read(self, size: int, /) -> bytes:
        """Return up to ``size`` bytes of the body."""


class BudgetExhaustedError(OSError):
    """Raised when the lookup's total time budget has run out."""


@dc.dataclass(slots=True)
class Deadline:
    """The single time budget a lookup shares across its attempts.

    Attributes
    ----------
    clock : Callable[[], float]
        Monotonic clock the budget is measured with.
    expires_at : float
        The clock reading at which the budget runs out.
    budget_seconds : float
        The whole budget, named in the error raised when it runs out.
    """

    clock: cabc.Callable[[], float]
    expires_at: float
    budget_seconds: float

    def remaining(self) -> float:
        """Return the seconds left, never negative."""
        return max(0.0, self.expires_at - self.clock())

    def check(self) -> None:
        """Raise when no time is left.

        Raises
        ------
        BudgetExhaustedError
            If the budget has run out.
        """
        if self.remaining() <= 0.0:
            message = f"the {self.budget_seconds:.0f}s time budget is exhausted"
            raise BudgetExhaustedError(message)


def declared_length(response: Response) -> int | None:
    """Return the response's ``Content-Length``, when it states a valid one.

    Returns
    -------
    int | None
        The declared length, or ``None`` when absent or not a number.
    """
    headers = getattr(response, "headers", None)
    raw = None if headers is None else headers.get("Content-Length")
    return int(raw) if raw is not None and str(raw).isdigit() else None


def _limit_socket_wait(response: Response, seconds: float) -> None:
    """Make the next blocking read on ``response`` wait at most ``seconds``.

    A buffered ``read(n)`` can loop over many ``recv`` calls, so the request
    timeout, capped at the budget left, is applied to the socket before every
    read and reads use ``read1``.
    That bounds each underlying read, not the total: a ``read1`` on a chunked
    body can still make several reads while it consumes the trailers. The
    deadline checks before a result is returned reject one that arrives late,
    but cannot interrupt a ``read1`` or a close already in progress. The
    socket is reached through the response's file object; a response without
    one (a test double) is left alone.
    """
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        sock.settimeout(seconds)


def read_chunk(response: Response, deadline: Deadline) -> bytes:
    """Read at most one chunk, never waiting past the deadline.

    Returns
    -------
    bytes
        The next chunk, or ``b""`` at the end of the body.

    Raises
    ------
    TimeoutError
        If the read timed out while budget remained (a retryable stall).
    """
    deadline.check()
    # A stall costs one request timeout, leaving budget for a retry.
    _limit_socket_wait(response, min(REQUEST_TIMEOUT_SECONDS, deadline.remaining()))
    read = getattr(response, "read1", response.read)
    try:
        return read(_READ_CHUNK_BYTES)
    except TimeoutError:
        deadline.check()  # Raises when the timeout was the budget running out.
        raise
