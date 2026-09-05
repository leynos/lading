"""Regression tests for subprocess output encoding boundaries."""

from __future__ import annotations

import io
import sys
import typing as typ
from importlib import import_module

from hypothesis import given
from hypothesis import strategies as st

from lading.runtime.subprocess_runner import relay_stream, write_to_sink

if typ.TYPE_CHECKING:
    import pytest

subprocess_runner_module = import_module("lading.runtime.subprocess_runner")

_UNICODE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), max_size=64
)


class _RecordingBuffer(io.BytesIO):
    """Record whether the binary fallback flushes its output."""

    def __init__(self, events: list[str]) -> None:
        """Initialize the buffer with a shared event log."""
        super().__init__()
        self._events = events
        self.is_flushed = False

    def write(self, payload: bytes) -> int:
        """Record and write binary payload bytes."""
        self._events.append("binary_write")
        return super().write(payload)

    def flush(self) -> None:
        """Record that the binary buffer was flushed."""
        self._events.append("binary_flush")
        self.is_flushed = True
        super().flush()


class _Cp1252Sink(io.TextIOBase):
    """Reject Unicode text while exposing a writable binary buffer."""

    def __init__(self) -> None:
        """Initialize the recording text sink and binary buffer."""
        self.events: list[str] = []
        self.buffer = _RecordingBuffer(self.events)
        self.flush_count = 0

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


class _TextOnlyCp1252Sink(io.TextIOBase):
    """Reject Unicode text without exposing a binary buffer."""

    def write(self, payload: str) -> int:
        """Validate that a payload fits the CP1252 text encoding."""
        return len(payload.encode("cp1252"))

    def flush(self) -> None:
        """Accept flush requests without writing output."""


class TestSubprocessRunnerEncoding:
    """Regression coverage for subprocess relay encoding boundaries."""

    def test_write_to_sink_falls_back_to_utf8_bytes(self) -> None:
        """Unicode rejected by a text sink should reach its binary buffer intact."""
        payload = "Cargo metadata: ś ń\n"
        sink = _Cp1252Sink()

        result = write_to_sink(sink, payload)

        assert result is sink, "encoding fallback should preserve the original sink"
        assert sink.flush_count == 1, "text sink should flush before binary fallback"
        assert sink.buffer.getvalue() == payload.encode("utf-8"), (
            "fallback should write exact UTF-8 bytes"
        )
        assert sink.buffer.is_flushed, "binary fallback should flush its buffer"
        assert sink.events == [
            "text_write",
            "text_flush",
            "binary_write",
            "binary_flush",
        ], "fallback should flush text before binary output"

    def test_write_to_sink_disables_text_only_sink_after_encoding_error(self) -> None:
        """An encoding-rejecting sink without a buffer should be disabled."""
        assert write_to_sink(_TextOnlyCp1252Sink(), "Cargo metadata: ś ń\n") is None, (
            "text-only sink should disable mirroring"
        )

    def test_relay_stream_preserves_unicode_capture_after_binary_fallback(
        self,
    ) -> None:
        """Mirroring through a binary fallback must not truncate captured output."""
        payload = "Cargo metadata: ś ń\n"
        source = io.BytesIO(payload.encode("utf-8"))
        sink = _Cp1252Sink()
        captured: list[str] = []

        relay_stream(source, sink, captured)

        assert "".join(captured) == payload, (
            "relay capture should preserve the complete Unicode payload"
        )
        assert sink.buffer.getvalue() == payload.encode("utf-8"), (
            "relay should mirror exact UTF-8 bytes"
        )

    def test_relay_stream_keeps_binary_mode_after_encoding_fallback(self) -> None:
        """Relay chunks after fallback should bypass the text sink."""
        initial_prefix = "initial é\n"
        initial_chunk = initial_prefix + "a" * (
            subprocess_runner_module._STREAM_CHUNK_SIZE
            - len(initial_prefix.encode("utf-8"))
        )
        fallback_prefix = "ś\n"
        fallback_chunk = fallback_prefix + "b" * (
            subprocess_runner_module._STREAM_CHUNK_SIZE
            - len(fallback_prefix.encode("utf-8"))
        )
        final_chunk = "later é\n"
        payload = initial_chunk + fallback_chunk + final_chunk
        source = io.BytesIO(payload.encode("utf-8"))
        sink = _Cp1252Sink()
        captured: list[str] = []

        relay_stream(source, sink, captured)

        expected_bytes = (
            initial_chunk.encode("cp1252")
            + fallback_chunk.encode("utf-8")
            + final_chunk.encode("utf-8")
        )
        assert "".join(captured) == payload, (
            "relay capture should preserve chunks across the encoding transition"
        )
        assert sink.buffer.getvalue() == expected_bytes, (
            "chunks after fallback should remain UTF-8 rather than return to CP1252"
        )

    def test_invoke_via_subprocess_preserves_unicode_with_narrow_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real child process should retain UTF-8 output after stream fallback."""
        payload = "Cargo metadata: ś ń\n"
        child_script = (
            f"import sys; sys.stdout.buffer.write({payload.encode('utf-8')!r})"
        )
        sink = _Cp1252Sink()
        monkeypatch.setattr(subprocess_runner_module.sys, "stdout", sink)

        exit_code, stdout, stderr = subprocess_runner_module.invoke_via_subprocess(
            sys.executable,
            ("-c", child_script),
            subprocess_runner_module.SubprocessContext(),
        )

        assert exit_code == 0, "child process should exit successfully"
        assert stdout == payload, "subprocess boundary should return complete stdout"
        assert stderr == "", "child process should not emit stderr"
        assert sink.buffer.getvalue() == payload.encode("utf-8"), (
            "subprocess relay should mirror exact UTF-8 bytes"
        )

    @given(payload=_UNICODE_TEXT)
    def test_write_to_sink_preserves_arbitrary_unicode_in_binary_fallback(
        self,
        payload: str,
    ) -> None:
        """Any payload containing a CP1252 gap should reach the binary buffer intact."""
        unicode_payload = f"{payload}ś"
        sink = _Cp1252Sink()

        result = write_to_sink(sink, unicode_payload)

        assert result is sink, "binary fallback should preserve the sink"
        assert sink.buffer.getvalue() == unicode_payload.encode("utf-8"), (
            "binary fallback should preserve arbitrary Unicode bytes"
        )

    @given(payload=_UNICODE_TEXT)
    def test_relay_stream_preserves_unicode_across_binary_transition(
        self,
        payload: str,
    ) -> None:
        """Large relay input should capture and mirror arbitrary Unicode exactly."""
        initial_chunk = "a" * subprocess_runner_module._STREAM_CHUNK_SIZE
        remainder = f"{payload}ślater é\n"
        expected_payload = initial_chunk + remainder
        source = io.BytesIO(expected_payload.encode("utf-8"))
        sink = _Cp1252Sink()
        captured: list[str] = []

        relay_stream(source, sink, captured)

        assert "".join(captured) == expected_payload, (
            "relay capture should preserve arbitrary Unicode across chunks"
        )
        assert sink.buffer.getvalue() == expected_payload.encode("utf-8"), (
            "binary mode should preserve UTF-8 output after the transition"
        )
