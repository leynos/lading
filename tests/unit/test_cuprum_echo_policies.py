"""Pin cuprum's broken-pipe policies and the per-stream echo they disable.

lading's relay contract is narrower than either cuprum policy alone: a closed
reader must not lose the child's output, must not stop the run, and must not
silence a stream whose destination still works. These tests exercise that
contract through cuprum's public API, because lading's own runner is still
subprocess-based (roadmap Phase 5.2) and this policy choice moves into it
unchanged.

Why the handshake
-----------------
A child that merely writes two chunks cannot prove that a failed write
*disabled* echo. The parent may read both chunks at once, so an implementation
that kept echoing would still perform a single write, and the assertion would
pass for the wrong reason.

Each child here therefore blocks on a named FIFO after flushing its first
chunk, and the sink releases that FIFO only while handling that stream's first
failed write. The second chunk cannot exist until the first write has already
failed, so a continued echo attempt becomes unavoidable rather than a matter of
scheduling. Nothing sleeps: the ordering is enforced by the kernel's FIFO
handshake, and ``run_child`` bounds the whole run with a timeout so a missing
release fails the test instead of hanging the suite.
"""

import collections.abc as cabc
import contextlib
import io
import os
import typing as typ
from pathlib import Path

import pytest
from cuprum import (
    BrokenPipePolicy,
    EchoErrorCategory,
    EchoStream,
    RelayFallback,
    RunOutputOptions,
)

from tests.helpers.cuprum_harness import run_child

if typ.TYPE_CHECKING:
    from cuprum import CommandResult
    from cuprum.sinks import OutputSession, SessionOutcome, SessionStart

#: The FIFO each stream's first failed write releases, keyed by the prefix of
#: the payload that write carries. Routing by payload is what makes the release
#: per-stream: the stdout failure opens only the stdout gate.
_GATE_VARIABLES = {
    "out-1": "LADING_ECHO_GATE_STDOUT",
    "err-1": "LADING_ECHO_GATE_STDERR",
}

#: Writes one chunk per stream, waits on that stream's gate if one is named,
#: then writes a second chunk. A stream with no gate in the environment is
#: written straight through, which is how the stdout-only test leaves stderr
#: unmetered.
_GATED_ECHO_CHILD = (
    "import os, sys\n"
    "def gate(name):\n"
    "    path = os.environ.get(name)\n"
    "    if path is None:\n"
    "        return\n"
    "    with open(path) as handle:\n"
    "        handle.read(1)\n"
    "sys.stdout.write('out-1\\n')\n"
    "sys.stdout.flush()\n"
    "gate('LADING_ECHO_GATE_STDOUT')\n"
    "sys.stdout.write('out-2\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stderr.write('err-1\\n')\n"
    "sys.stderr.flush()\n"
    "gate('LADING_ECHO_GATE_STDERR')\n"
    "sys.stderr.write('err-2\\n')\n"
    "sys.stderr.flush()\n"
)

#: The ungated child writes both chunks immediately. Strict mode stops the run
#: at the first failed write, so no second attempt is possible however the
#: parent schedules its reads; that is what makes this child the right one for
#: the strict test and the wrong one for the best-effort tests.
_ECHO_CHILD = (
    "import sys\n"
    "sys.stdout.write('out-1\\n'); sys.stdout.flush()\n"
    "sys.stdout.write('out-2\\n'); sys.stdout.flush()\n"
    "sys.stderr.write('err-1\\n'); sys.stderr.flush()\n"
    "sys.stderr.write('err-2\\n'); sys.stderr.flush()\n"
)


class _EchoGate:
    """One FIFO whose release signals that a stream's first write has failed.

    The parent holds the FIFO open read-write. That keeps a reader present, so
    the child's blocking ``open`` returns at once instead of waiting for a
    writer, and it means the release write can never raise ``EPIPE``. The
    parent never reads, so the release byte stays buffered for the child.
    """

    def __init__(self, path: Path) -> None:
        """Create the FIFO at ``path`` and hold it open for ``release``."""
        self.path = path
        os.mkfifo(path)
        self._descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def release(self) -> None:
        """Let the child past its gate by buffering one byte."""
        os.write(self._descriptor, b"x")

    def close(self) -> None:
        """Release the descriptor so the test leaks no file handle."""
        os.close(self._descriptor)


class _BrokenPipeWriter(io.TextIOBase):
    """A parent destination whose reader has closed its end of the pipe.

    Each write is refused as a closed pipe would be, and each stream's first
    refusal releases that stream's gate: the release is what allows the child's
    second chunk to exist at all.
    """

    def __init__(self, gates: cabc.Mapping[str, _EchoGate]) -> None:
        """Bind the writer to the gates its failed writes release."""
        super().__init__()
        self.write_attempts = 0
        self._gates = gates

    def write(self, payload: str, /) -> int:
        """Refuse the write as a closed pipe would, recording the attempt."""
        self.write_attempts += 1
        self._release(payload)
        raise BrokenPipeError

    def _release(self, payload: str) -> None:
        """Open the gate for the stream this payload belongs to, if any."""
        for prefix, gate in self._gates.items():
            if payload.startswith(prefix):
                gate.release()
                return

    def flush(self) -> None:
        """Refuse the flush as a closed pipe would."""
        raise BrokenPipeError

    def close(self) -> None:
        """Close without flushing, so interpreter teardown cannot re-raise.

        ``io.IOBase.__del__`` calls ``close()``, and the inherited ``close``
        flushes. An unraisable ``BrokenPipeError`` at garbage collection would
        be attributed to whichever test happened to drop the last reference.
        """


class _RecordingSession:
    """One presentation session for a run whose sink cannot accept output."""

    def __init__(self, writer: _BrokenPipeWriter) -> None:
        """Bind the session to the writer it exposes as its log."""
        self._writer = writer
        self.is_closed = False
        self.outcome: SessionOutcome | None = None

    @property
    def log(self) -> typ.IO[str]:
        """The destination echoed output is routed through."""
        # ``IO`` is nominal in typeshed; the double is duck-typed.
        return typ.cast("typ.IO[str]", self._writer)

    def close(self, outcome: SessionOutcome) -> None:
        """Record the terminal report without retrying a failed write."""
        self.outcome = outcome
        self.is_closed = True


class _BrokenPipeSink:
    """A conforming sink whose destination has gone away.

    It deliberately has no ``buffer`` attribute: cuprum bypasses a sink that
    exposes one and writes raw bytes, so a byte-buffer double would never
    reach the text path under test.
    """

    def __init__(self, gates: cabc.Mapping[str, _EchoGate]) -> None:
        """Create a writer over ``gates`` and no session."""
        self.writer = _BrokenPipeWriter(gates)
        self.session: _RecordingSession | None = None

    def open_session(self, start: SessionStart) -> OutputSession | None:
        """Open one session for the run and record it."""
        del start
        self.session = _RecordingSession(self.writer)
        return self.session


@contextlib.contextmanager
def _open_gates(
    prefixes: cabc.Sequence[str], directory: Path
) -> cabc.Generator[dict[str, _EchoGate]]:
    """Create one gate per prefix, closing every created gate afterwards.

    Closing in ``finally`` keeps the file handles bounded even when the run
    under test raises, which matters because a leaked descriptor would outlive
    the test that produced it.

    Yields
    ------
    dict[str, _EchoGate]
        The open gates, keyed by payload prefix.
    """
    gates: dict[str, _EchoGate] = {}
    try:
        for prefix in prefixes:
            gates[prefix] = _EchoGate(directory / f"{prefix}.fifo")
        yield gates
    finally:
        for gate in gates.values():
            gate.close()


def _run_gated(
    directory: Path,
    policy: BrokenPipePolicy,
    *,
    echo_stderr: bool,
) -> tuple[CommandResult, _BrokenPipeSink]:
    """Run the gated child against a sink that releases each stream's gate.

    Only a stream that will actually be echoed gets a gate: an echoed stream
    with no gate would leave the child blocked until the timeout, and an
    un-echoed stream with one would never be released at all.

    Returns
    -------
    tuple[CommandResult, _BrokenPipeSink]
        The child's result and the sink its echo was routed through.
    """
    prefixes = ("out-1", "err-1") if echo_stderr else ("out-1",)
    with _open_gates(prefixes, directory) as gates:
        sink = _BrokenPipeSink(gates)
        result = run_child(
            _GATED_ECHO_CHILD,
            output=RunOutputOptions(
                capture=True,
                echo=True,
                echo_stderr=echo_stderr,
                sink=sink,
                broken_pipe_policy=policy,
            ),
            env={
                _GATE_VARIABLES[prefix]: str(gates[prefix].path) for prefix in prefixes
            },
        )
    return result, sink


def test_best_effort_keeps_capture_when_the_sink_breaks(tmp_path: Path) -> None:
    """A closed reader stops echo while capture and the exit code survive.

    This is lading's relay policy expressed natively: one failed write disables
    that stream's mirroring, later chunks are not retried, and the child's full
    output still returns in the result.

    The child cannot write its second chunk until the sink has handled the
    first failed write, so the single write attempt is caused by the disablement
    rather than by both chunks arriving in one read.
    """
    result, sink = _run_gated(tmp_path, BrokenPipePolicy.BEST_EFFORT, echo_stderr=False)

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "out-1\nout-2\n", result.stdout
    assert result.stderr == "err-1\nerr-2\n", result.stderr
    assert result.relay_fallbacks == (
        RelayFallback(
            stream=EchoStream.STDOUT,
            error_category=EchoErrorCategory.BROKEN_PIPE,
        ),
    ), result.relay_fallbacks
    assert sink.writer.write_attempts == 1, "echo must not retry a failed write"
    assert sink.session is not None, "the run opened no session"
    assert sink.session.is_closed, "the session must be closed on every path"


def test_best_effort_disables_only_the_stream_that_broke(tmp_path: Path) -> None:
    """One broken destination does not silence the other stream.

    ``echo_stderr`` mirrors both streams through the same sink, so both writes
    fail. Each failing stream reports its own fallback and is attempted exactly
    once, and both streams are still fully captured.

    Both gates are released by their own stream's failure, so each stream's
    second chunk is written only after that stream has been unmirrored.
    """
    result, sink = _run_gated(tmp_path, BrokenPipePolicy.BEST_EFFORT, echo_stderr=True)

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "out-1\nout-2\n", result.stdout
    assert result.stderr == "err-1\nerr-2\n", result.stderr
    assert result.relay_fallbacks == (
        RelayFallback(
            stream=EchoStream.STDOUT,
            error_category=EchoErrorCategory.BROKEN_PIPE,
        ),
        RelayFallback(
            stream=EchoStream.STDERR,
            error_category=EchoErrorCategory.BROKEN_PIPE,
        ),
    ), result.relay_fallbacks
    assert sink.writer.write_attempts == 2, (
        "each stream must attempt exactly one write before it is unmirrored"
    )


def test_strict_broken_pipe_policy_propagates() -> None:
    """The default policy is strict, which is why the opt-in is load-bearing.

    A future reader might reasonably assume BEST_EFFORT is the default; the
    raise here records that it is not, and that the neighbouring tests prove
    the opt-in rather than an unchanged default.

    This child is ungated: strict mode stops the run at the first failure, so
    no second attempt is possible whatever the read boundaries are. The
    assertion is therefore that the sink was reached at all, not how many times
    it was reached: cuprum's timeout teardown drains the remaining streams
    after the raise, and the number of writes that produces depends on how the
    child's chunks happened to coalesce.
    """
    sink = _BrokenPipeSink({})

    with pytest.raises(BrokenPipeError):
        run_child(
            _ECHO_CHILD,
            output=RunOutputOptions(
                capture=True,
                echo=True,
                sink=sink,
                broken_pipe_policy=BrokenPipePolicy.STRICT,
            ),
        )

    assert sink.writer.write_attempts >= 1, "the raise must come from the echo path"
