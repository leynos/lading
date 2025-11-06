"""Unit tests for streaming helpers in ``run_publish_check``."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest

from .conftest import StreamRecorder, _ChunkedStream


def test_drain_stream_handles_partial_utf8(
    run_publish_check_module: ModuleType,
) -> None:
    """Incremental decoder should stitch multi-byte UTF-8 sequences."""
    stream = _ChunkedStream([b"\xf0\x9f", b"\x92\xa9"])
    sink = StreamRecorder()
    buffer: list[str] = []

    run_publish_check_module._drain_stream(stream, sink, buffer)

    assert "".join(buffer) == "💩"
    assert sink.writes == ["💩"]
    assert sink.flush_count == 1


def test_drain_stream_handles_empty_chunked_stream(
    run_publish_check_module: ModuleType,
) -> None:
    """Empty streams should not emit writes or trigger flushes."""
    stream = _ChunkedStream([])
    sink = StreamRecorder()
    buffer: list[str] = []

    run_publish_check_module._drain_stream(stream, sink, buffer)

    assert buffer == []
    assert sink.writes == []
    assert sink.flush_count == 0


def test_stream_process_output_streams_chunks(
    run_publish_check_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_stream_process_output should mirror stdout/stderr chunks immediately."""
    stdout_stream = _ChunkedStream([b"one", b"two"])
    stderr_stream = _ChunkedStream([b"warn"])
    process = SimpleNamespace(
        stdout=stdout_stream,
        stderr=stderr_stream,
        args=["cargo", "mock"],
    )

    def wait(timeout: int | None = None) -> int:
        assert timeout == 3
        return 0

    process.wait = wait  # type: ignore[attr-defined]
    process.kill = lambda: None  # type: ignore[attr-defined]

    stdout_recorder = StreamRecorder()
    stderr_recorder = StreamRecorder()
    monkeypatch.setattr(run_publish_check_module.sys, "stdout", stdout_recorder)
    monkeypatch.setattr(run_publish_check_module.sys, "stderr", stderr_recorder)

    result = run_publish_check_module._stream_process_output(
        process,
        ("cargo", "mock"),
        timeout_secs=3,
    )

    assert result.stdout == "onetwo"
    assert result.stderr == "warn"
    assert stdout_recorder.writes == ["one", "two"]
    assert stderr_recorder.writes == ["warn"]
    assert stdout_recorder.flush_count == 2
    assert stderr_recorder.flush_count == 1


def test_stream_process_output_handles_mixed_encoding(
    run_publish_check_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed encodings should replace undecodable bytes without crashing."""
    stdout_stream = _ChunkedStream([b"ok", b"\xff\xfe", b"done"])
    stderr_stream = _ChunkedStream([b"warn", b"\xff", b"error"])
    process = SimpleNamespace(
        stdout=stdout_stream,
        stderr=stderr_stream,
        args=["cargo", "mock"],
    )

    process.wait = lambda timeout=None: 0  # type: ignore[attr-defined]
    process.kill = lambda: None  # type: ignore[attr-defined]

    stdout_recorder = StreamRecorder()
    stderr_recorder = StreamRecorder()
    monkeypatch.setattr(run_publish_check_module.sys, "stdout", stdout_recorder)
    monkeypatch.setattr(run_publish_check_module.sys, "stderr", stderr_recorder)

    result = run_publish_check_module._stream_process_output(
        process,
        ("cargo", "mock"),
        timeout_secs=3,
    )

    assert "\ufffd" in result.stdout
    assert "\ufffd" in result.stderr
    assert "".join(stdout_recorder.writes) == result.stdout
    assert "".join(stderr_recorder.writes) == result.stderr


def test_stream_process_output_cleans_up_on_timeout(
    run_publish_check_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeouts should kill the process, close streams, and re-raise."""
    stdout_stream = _ChunkedStream([b"partial"])
    stderr_stream = _ChunkedStream([b"error"])
    state = {"kill": False, "wait": 0}

    def wait(timeout: int | None = None) -> None:
        raise run_publish_check_module.subprocess.TimeoutExpired(cmd="cargo", timeout=1)

    def kill() -> None:
        state["kill"] = True

    process = SimpleNamespace(
        stdout=stdout_stream,
        stderr=stderr_stream,
        wait=wait,
        kill=kill,
        args=["cargo"],
    )

    stdout_recorder = StreamRecorder()
    stderr_recorder = StreamRecorder()
    monkeypatch.setattr(run_publish_check_module.sys, "stdout", stdout_recorder)
    monkeypatch.setattr(run_publish_check_module.sys, "stderr", stderr_recorder)

    with pytest.raises(run_publish_check_module.ProcessTimedOut):
        run_publish_check_module._stream_process_output(
            process,
            ("cargo", "mock"),
            timeout_secs=3,
        )

    assert state["kill"] is True
    assert stdout_stream.closed is True
    assert stderr_stream.closed is True


def test_start_stream_threads_creates_workers(
    run_publish_check_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure _start_stream_threads spins up a thread per stream."""
    created = []

    class DummyThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.started = False
            created.append(self)

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(run_publish_check_module.threading, "Thread", DummyThread)

    process = SimpleNamespace(stdout=object(), stderr=object())
    threads, stdout_chunks, stderr_chunks = (
        run_publish_check_module._start_stream_threads(process)
    )

    assert len(created) == 2
    assert all(thread.started for thread in created)
    assert threads == created
    assert stdout_chunks == []
    assert stderr_chunks == []


def test_wait_for_stream_threads_joins_all(
    run_publish_check_module: ModuleType,
) -> None:
    """_wait_for_stream_threads must join each supplied thread."""

    class DummyThread:
        def __init__(self) -> None:
            self.join_calls = 0

        def join(self) -> None:
            self.join_calls += 1

    threads = [DummyThread(), DummyThread()]
    run_publish_check_module._wait_for_stream_threads(threads)
    assert [thread.join_calls for thread in threads] == [1, 1]


def test_close_process_streams_handles_both_pipes(
    run_publish_check_module: ModuleType,
) -> None:
    """_close_process_streams should close stdout and stderr when present."""

    class DummyStream:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    process = SimpleNamespace(stdout=DummyStream(), stderr=DummyStream())
    run_publish_check_module._close_process_streams(process)
    assert process.stdout.closed
    assert process.stderr.closed


def test_handle_process_timeout_cleans_threads_and_streams(
    run_publish_check_module: ModuleType,
) -> None:
    """_handle_process_timeout should terminate the process and close streams."""

    class DummyStream:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DummyProcess:
        def __init__(self) -> None:
            self.stdout = DummyStream()
            self.stderr = DummyStream()
            self.kill_called = False
            self.wait_called = False
            self.args = ["cargo", "mock"]

        def kill(self) -> None:
            self.kill_called = True

        def wait(self) -> None:
            self.wait_called = True

    class DummyThread:
        def __init__(self) -> None:
            self.join_args: list[float | None] = []

        def join(self, timeout: float | None = None) -> None:
            self.join_args.append(timeout)

    process = DummyProcess()
    threads = [DummyThread()]
    error = run_publish_check_module.subprocess.TimeoutExpired(cmd="cargo", timeout=1)

    with pytest.raises(run_publish_check_module.ProcessTimedOut):
        run_publish_check_module._handle_process_timeout(
            process,
            threads,
            ("cargo", "mock"),
            error,
        )

    assert process.kill_called is True
    assert process.wait_called is True
    assert process.stdout.closed
    assert process.stderr.closed
    assert threads[0].join_args == [0.1]


def test_stream_recorder_captures_writes_and_flushes() -> None:
    """Ensure the recorder stores writes and tracks flush counters."""
    recorder = StreamRecorder()
    assert recorder.write("hello") == 5
    recorder.flush()
    recorder.write("world")
    recorder.flush()

    assert recorder.writes == ["hello", "world"]
    assert recorder.flush_count == 2
    assert recorder.flushes == recorder.flush_count


def test_stream_recorder_flush_does_not_require_writes() -> None:
    """Flushing without writes should still increment the counter."""
    recorder = StreamRecorder()
    recorder.flush()
    recorder.flush()

    assert recorder.flush_count == 2
    assert recorder.writes == []
