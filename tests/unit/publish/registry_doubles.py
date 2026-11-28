"""Test doubles shared by the crates.io archive digest fetcher tests.

``Script`` replays scripted responses or failures, one per request. ``Clock``,
``SlowSocket`` and ``SlowBody`` model a response whose reads take scripted
time, so the budget tests can prove where the deadline fires without sleeping.
"""

import contextlib
import email.message
import io
import types
import typing as typ
import urllib.error
import urllib.request

from lading.commands.publish_registry import FetchTransport

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class Script:
    """Replay a scripted sequence of responses or failures, recording requests."""

    def __init__(self, *steps: bytes | BaseException) -> None:
        """Store the steps to replay, one per request."""
        self._steps = list(steps)
        self.requests: list[urllib.request.Request] = []
        self.sleeps: list[float] = []

    def transport(self) -> FetchTransport:
        """Return a transport that replays the script and records the waits."""
        return FetchTransport(opener=self.open_url, sleep=self.sleeps.append)

    @contextlib.contextmanager
    def open_url(
        self, request: urllib.request.Request, *, timeout: float
    ) -> cabc.Generator[io.BytesIO]:
        """Replay the next step: raise it, or serve its bytes."""
        del timeout
        self.requests.append(request)
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        yield io.BytesIO(step)

    def next_step(self) -> bytes | BaseException:
        """Remove and return the next scripted step."""
        return self._steps.pop(0)


def http_error(code: int, body: io.BytesIO | None = None) -> urllib.error.HTTPError:
    """Return an HTTP error with status ``code``, owning ``body`` if given."""
    return urllib.error.HTTPError(
        "https://example.invalid", code, "x", email.message.Message(), body
    )


class Clock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        """Start at zero, having been read no times."""
        self.now = 0.0
        self.reads = 0

    def __call__(self) -> float:
        """Return the current time, counting the read."""
        self.reads += 1
        return self.now


class SlowSocket:
    """A socket double: each underlying read waits its delay unless cut short."""

    def __init__(self, clock: Clock, delays: list[float | tuple[float, ...]]) -> None:
        """Wait ``delays[i]`` for the ``i``-th ``read1``.

        A tuple stands for one ``read1`` that makes several underlying reads,
        as CPython's chunked reader does while it consumes the trailers; each
        is bounded by the socket timeout on its own.
        """
        self.clock = clock
        self.delays = delays
        self.timeouts: list[float] = []
        self.timeout = 0.0

    def settimeout(self, seconds: float) -> None:
        """Record the wait the next read may take at most."""
        self.timeouts.append(seconds)
        self.timeout = seconds

    def wait(self, delay: float) -> None:
        """Spend ``delay`` on one underlying read, or time out at the limit.

        Raises
        ------
        TimeoutError
            If ``delay`` exceeds the socket timeout.
        """
        if delay > self.timeout:
            self.clock.now += self.timeout
            message = "timed out"
            raise TimeoutError(message)
        self.clock.now += delay


class SlowBody:
    """A response whose reads each take a scripted time, like a slow trickle."""

    def __init__(self, sock: SlowSocket, payloads: list[bytes] | None = None) -> None:
        """Expose ``sock`` where HTTPResponse keeps it (``fp.raw._sock``).

        Each ``read1`` returns the matching entry of ``payloads`` (``b""`` is
        the end of the body), or one byte when no payloads are given.
        """
        self.fp = types.SimpleNamespace(raw=types.SimpleNamespace(_sock=sock))
        self._sock = sock
        self._payloads = payloads
        self.reads = 0

    def read1(self, size: int = -1) -> bytes:
        """Return the next payload after the scripted delay, or time out."""
        del size
        delay = self._sock.delays[self.reads]
        payload = b"x" if self._payloads is None else self._payloads[self.reads]
        self.reads += 1
        for underlying in delay if isinstance(delay, tuple) else (delay,):
            self._sock.wait(underlying)
        return payload

    def read(self, size: int = -1) -> bytes:
        """Fail: a buffered read must never be used."""
        del size
        message = "read() could outlive the budget; read1() must be used"
        raise AssertionError(message)


def body_transport(clock: Clock, body: SlowBody) -> FetchTransport:
    """Return a transport serving ``body`` and recording nothing else."""

    @contextlib.contextmanager
    def opener(
        request: urllib.request.Request, /, *, timeout: float
    ) -> cabc.Generator[SlowBody]:
        """Open the scripted body."""
        del request, timeout
        yield body

    return FetchTransport(opener=opener, sleep=lambda _s: None, clock=clock)
