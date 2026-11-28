"""Unit tests for the crates.io digest fetcher's single time budget.

The fetcher shares one deadline across connecting, every read, the retry waits
and closing. These tests drive it with scripted clocks and slow sockets, so
they prove where the deadline fires without waiting for it.
"""

import contextlib
import io
import typing as typ
import urllib.error
import urllib.request

import pytest

from lading.commands import publish_registry
from lading.commands.publish_registry import (
    FetchTransport,
    PublishedArchiveUnavailableError,
    fetch_crates_io_digest,
)
from tests.helpers.publish_digests import PLAIN_ARCHIVE, digest
from tests.unit.publish.registry_doubles import (
    Clock,
    Script,
    SlowBody,
    SlowSocket,
    body_transport,
    http_error,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def test_total_time_budget_bounds_a_slow_trickle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steady delivery cannot extend the one deadline for the whole lookup."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    now = [0.0]

    class _Trickle(io.RawIOBase):
        """Deliver one byte per read, each costing a second."""

        def read(self, size: int = -1) -> bytes:
            """Advance the clock and return one byte."""
            now[0] += 1.0
            return b"x"

    @contextlib.contextmanager
    def opener(
        request: urllib.request.Request, *, timeout: float
    ) -> cabc.Generator[_Trickle]:
        """Open a body that never ends."""
        del request, timeout
        yield _Trickle()

    transport = FetchTransport(
        opener=opener, sleep=lambda _s: None, clock=lambda: now[0]
    )

    with pytest.raises(PublishedArchiveUnavailableError, match="time budget"):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)


def test_retry_waits_count_against_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry that would outlive the budget is not attempted."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 3.0)
    script = Script(*[http_error(503)] * 3)
    now = [0.0]

    def sleep(seconds: float) -> None:
        """Advance the clock by the wait."""
        now[0] += seconds
        script.sleeps.append(seconds)

    transport = FetchTransport(
        opener=script.open_url, sleep=sleep, clock=lambda: now[0]
    )

    with pytest.raises(PublishedArchiveUnavailableError):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert len(script.requests) == 2, "the third attempt would exceed the budget"


def test_the_budget_fires_in_the_middle_of_a_slow_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single read that would outlast the budget is cut off at the budget."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    clock = Clock()
    sock = SlowSocket(clock, delays=[1.0, 100.0])
    transport = body_transport(clock, SlowBody(sock))

    with pytest.raises(PublishedArchiveUnavailableError, match="time budget"):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert sock.timeouts[:2] == [5.0, 4.0], (
        "each read must be limited to what the budget has left at that moment"
    )
    assert clock.now == pytest.approx(5.0), (
        "the stalled read must stop at the budget, not run its full 100 seconds"
    )


def test_expiry_while_connecting_is_a_timeout_not_a_404_or_a_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect or header phase that spends the budget fails as a timeout."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    clock = Clock()
    asked: list[float] = []

    @contextlib.contextmanager
    def opener(
        request: urllib.request.Request, /, *, timeout: float
    ) -> cabc.Generator[io.BytesIO]:
        """Spend the whole budget before any response arrives."""
        del request
        asked.append(timeout)
        clock.now += 5.0
        raise urllib.error.URLError(TimeoutError("timed out"))
        yield io.BytesIO()  # pragma: no cover - unreachable by design

    transport = FetchTransport(opener=opener, sleep=lambda _s: None, clock=clock)

    with pytest.raises(PublishedArchiveUnavailableError, match="time budget"):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert asked == [5.0], "the open must be given the remaining budget, once"


def _late_transport(clock: Clock, script: Script) -> FetchTransport:
    """Return a transport that answers from ``script`` only after 6 seconds."""

    @contextlib.contextmanager
    def opener(
        request: urllib.request.Request, /, *, timeout: float
    ) -> cabc.Generator[io.BytesIO]:
        """Spend the time, then replay the next scripted step."""
        clock.now += 6.0
        with script.open_url(request, timeout=timeout) as response:
            yield response

    return FetchTransport(opener=opener, sleep=lambda _s: None, clock=clock)


@pytest.mark.parametrize(
    "answer",
    [PLAIN_ARCHIVE, http_error(404)],
    ids=["complete_body", "not_found"],
)
def test_an_answer_that_arrives_after_the_deadline_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, answer: bytes | BaseException
) -> None:
    """Late headers are never a digest, and a late 404 is never "unpublished"."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    transport = _late_transport(Clock(), Script(answer))

    with pytest.raises(PublishedArchiveUnavailableError, match="time budget"):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)


def test_a_retry_wait_is_clamped_to_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pause between attempts never exceeds what the budget has left."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 2.5)
    monkeypatch.setattr(publish_registry, "_RETRY_DELAY_SECONDS", 2.0)
    clock = Clock()
    script = Script(http_error(503), PLAIN_ARCHIVE)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        """Record the wait and spend it."""
        waits.append(seconds)
        clock.now += seconds

    transport = FetchTransport(opener=script.open_url, sleep=sleep, clock=clock)

    result = fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert result == digest(PLAIN_ARCHIVE), "the retry within the budget succeeds"
    assert all(wait <= 2.5 for wait in waits), "no wait may exceed the budget"


def test_an_end_of_body_that_arrives_after_the_deadline_is_not_a_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late EOF fails as the budget expiring, never as a digest.

    The final ``read1`` makes two underlying reads, as CPython's chunked
    reader does for the trailers; each stays within the socket timeout, but
    together they end past the deadline.
    """
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    clock = Clock()
    sock = SlowSocket(clock, delays=[1.0, (3.0, 3.0)])
    transport = body_transport(clock, SlowBody(sock, payloads=[b"x", b""]))

    with pytest.raises(PublishedArchiveUnavailableError, match="time budget"):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert clock.now == pytest.approx(7.0), "the EOF itself arrived late"


def test_a_close_that_runs_past_the_deadline_is_not_a_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup that finishes late fails as the budget expiring."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    clock = Clock()
    closed: list[float] = []

    @contextlib.contextmanager
    def opener(
        request: urllib.request.Request, /, *, timeout: float
    ) -> cabc.Generator[io.BytesIO]:
        """Serve the whole body promptly, then take too long to close."""
        del request, timeout
        try:
            yield io.BytesIO(PLAIN_ARCHIVE)
        finally:
            clock.now += 6.0
            closed.append(clock.now)

    transport = FetchTransport(opener=opener, sleep=lambda _s: None, clock=clock)

    with pytest.raises(PublishedArchiveUnavailableError, match="time budget"):
        fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert closed == [6.0], "the response must have been closed, once"


def test_a_not_found_within_the_budget_is_still_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final check does not disturb a 404 that arrives in time."""
    monkeypatch.setattr(publish_registry, "_TOTAL_BUDGET_SECONDS", 5.0)
    clock = Clock()
    script = Script(http_error(404))
    transport = FetchTransport(
        opener=script.open_url, sleep=lambda _s: None, clock=clock
    )

    assert fetch_crates_io_digest("a", "1.0.0", transport=transport) is None, (
        "a timely 404 still means not published"
    )


def test_a_stalled_body_is_cut_at_the_request_timeout_and_retried() -> None:
    """A stall costs one request timeout, not the whole budget, and is retried."""
    clock = Clock()
    stalled_socket = SlowSocket(clock, delays=[1000.0])
    stalled = SlowBody(stalled_socket)
    healthy = SlowBody(SlowSocket(clock, delays=[1.0, 1.0]), [PLAIN_ARCHIVE, b""])
    bodies = [stalled, healthy]

    @contextlib.contextmanager
    def opener(
        request: urllib.request.Request, /, *, timeout: float
    ) -> cabc.Generator[SlowBody]:
        """Serve the stalled body first, then the healthy one."""
        del request, timeout
        yield bodies.pop(0)

    transport = FetchTransport(opener=opener, sleep=lambda _s: None, clock=clock)

    result = fetch_crates_io_digest("a", "1.0.0", transport=transport)

    assert result == digest(PLAIN_ARCHIVE), "the retry within the budget succeeds"
    assert stalled_socket.timeouts == [60.0], (
        "a read may wait one request timeout, not the whole budget"
    )
