"""Test doubles for parent streams used by the subprocess output relay."""

import collections.abc as cabc
import io
import typing as typ


class _RecordingBuffer(io.BytesIO):
    """Record whether the binary fallback flushes its output."""

    def __init__(self, events: list[str]) -> None:
        """Initialize the buffer with a shared event log."""
        super().__init__()
        self._events = events
        self.is_flushed = False

    def write(self, payload: cabc.Buffer) -> int:
        """Record and write binary payload bytes."""
        self._events.append("binary_write")
        return super().write(payload)

    def flush(self) -> None:
        """Record that the binary buffer was flushed."""
        self._events.append("binary_flush")
        self.is_flushed = True
        super().flush()


class _BrokenPipeRecordingBuffer(_RecordingBuffer):
    """Raise a broken pipe at the selected binary relay operation."""

    def __init__(
        self, events: list[str], failure_stage: typ.Literal["write", "flush"]
    ) -> None:
        """Initialize the buffer with one selected broken-pipe stage."""
        super().__init__(events)
        self.failure_stage = failure_stage

    def write(self, payload: cabc.Buffer) -> int:
        """Raise at binary write when selected, otherwise record the payload."""
        if self.failure_stage == "write":
            self._events.append("binary_write")
            raise BrokenPipeError
        return super().write(payload)

    def flush(self) -> None:
        """Raise at binary flush when selected, otherwise flush normally."""
        if self.failure_stage == "flush":
            self._events.append("binary_flush")
            raise BrokenPipeError
        super().flush()


class _Cp1252Sink(io.TextIOBase):
    """Reject Unicode text while exposing a writable binary buffer."""

    def __init__(
        self,
        *,
        binary_failure_stage: typ.Literal["write", "flush"] | None = None,
        text_flush_broken_pipe: bool = False,
    ) -> None:
        """Initialize the recording text sink and binary buffer."""
        self.events: list[str] = []
        self.buffer: _RecordingBuffer
        if binary_failure_stage is None:
            self.buffer = _RecordingBuffer(self.events)
        else:
            self.buffer = _BrokenPipeRecordingBuffer(self.events, binary_failure_stage)
        self.flush_count = 0
        self.text_flush_broken_pipe = text_flush_broken_pipe

    def write(self, payload: str) -> int:
        """Encode accepted text as CP1252 bytes."""
        self.events.append("text_write")
        encoded = payload.encode("cp1252")
        self.buffer.write(encoded)
        return len(payload)

    def flush(self) -> None:
        """Record that the text sink was flushed."""
        self.events.append("text_flush")
        self.flush_count += 1
        if self.text_flush_broken_pipe:
            self.text_flush_broken_pipe = False
            raise BrokenPipeError


class _TextOnlyCp1252Sink(io.TextIOBase):
    """Reject Unicode text without exposing a binary buffer."""

    def write(self, payload: str) -> int:
        """Validate that a payload fits the CP1252 text encoding."""
        return len(payload.encode("cp1252"))

    def flush(self) -> None:
        """Accept flush requests without writing output."""


class _BrokenPipeSink:
    """Reject every parent-stream write with a broken pipe."""

    def write(self, payload: str, /) -> int:
        """Raise BrokenPipeError without retaining the relay payload."""
        del payload
        raise BrokenPipeError

    def flush(self, /) -> None:
        """Raise BrokenPipeError if the relay attempts a flush."""
        raise BrokenPipeError
