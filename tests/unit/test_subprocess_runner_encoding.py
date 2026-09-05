"""Regression tests for subprocess output encoding boundaries."""

from __future__ import annotations

import io

from lading.runtime.subprocess_runner import relay_stream, write_to_sink


class _RecordingBuffer(io.BytesIO):
    """Record whether the binary fallback flushes its output."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events
        self.is_flushed = False

    def write(self, payload: bytes) -> int:
        self._events.append("binary_write")
        return super().write(payload)

    def flush(self) -> None:
        self._events.append("binary_flush")
        self.is_flushed = True
        super().flush()


class _Cp1252Sink(io.TextIOBase):
    """Reject Unicode text while exposing a writable binary buffer."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.buffer = _RecordingBuffer(self.events)
        self.flush_count = 0

    def write(self, payload: str) -> int:
        self.events.append("text_write")
        return len(payload.encode("cp1252"))

    def flush(self) -> None:
        self.events.append("text_flush")
        self.flush_count += 1


class _TextOnlyCp1252Sink(io.TextIOBase):
    """Reject Unicode text without exposing a binary buffer."""

    def write(self, payload: str) -> int:
        return len(payload.encode("cp1252"))

    def flush(self) -> None:
        return None


def test_write_to_sink_falls_back_to_utf8_bytes() -> None:
    """Unicode rejected by a text sink should reach its binary buffer intact."""
    payload = "Cargo metadata: ś ń\n"
    sink = _Cp1252Sink()

    result = write_to_sink(sink, payload)

    assert result is sink
    assert sink.flush_count == 1
    assert sink.buffer.getvalue() == payload.encode("utf-8")
    assert sink.buffer.is_flushed
    assert sink.events == [
        "text_write",
        "text_flush",
        "binary_write",
        "binary_flush",
    ]


def test_write_to_sink_disables_text_only_sink_after_encoding_error() -> None:
    """An encoding-rejecting sink without a buffer should be disabled."""
    assert write_to_sink(_TextOnlyCp1252Sink(), "Cargo metadata: ś ń\n") is None


def test_relay_stream_preserves_unicode_capture_after_binary_fallback() -> None:
    """Mirroring through a binary fallback must not truncate captured output."""
    payload = "Cargo metadata: ś ń\n"
    source = io.BytesIO(payload.encode("utf-8"))
    sink = _Cp1252Sink()
    captured: list[str] = []

    relay_stream(source, sink, captured)

    assert "".join(captured) == payload
    assert sink.buffer.getvalue() == payload.encode("utf-8")
