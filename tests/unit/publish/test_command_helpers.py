"""Unit tests for publish command execution helpers."""

from __future__ import annotations

import io
from pathlib import Path

from lading.commands import publish_execution as execution


def test_normalise_environment_handles_none_and_values() -> None:
    """Environment normalisation should coerce values to strings."""
    assert execution._normalise_environment(None) is None
    assert execution._normalise_environment({"ALPHA": 1}) == {"ALPHA": "1"}


def test_format_thread_name_sanitises_paths() -> None:
    """Thread names derived from program paths drop separators."""
    name = execution._format_thread_name(str(Path("foo") / "tools" / "cargo"), "stdout")
    assert "stdout" in name
    assert "/" not in name


def test_redact_environment_masks_sensitive_keys() -> None:
    """Environment redaction should hide sensitive token-like variables."""
    sensitive_key = "_".join(("ACCESS", "TOKEN"))
    payload = {
        sensitive_key: "example-token",
        "harmless": "value",
    }
    redacted = execution._redact_environment(payload)
    assert redacted[sensitive_key] == "<redacted>"
    assert redacted["harmless"] == "value"


def test_relay_stream_forwards_and_decodes_bytes() -> None:
    """Relay helper should decode bytes and echo them into sinks."""
    source = io.BytesIO(b"alpha")
    sink = io.StringIO()
    buffer: list[str] = []

    execution._relay_stream(source, sink, buffer)

    assert buffer == ["alpha"]
    assert sink.getvalue() == "alpha"


def test_write_to_sink_handles_broken_pipe() -> None:
    """Broken pipe errors should be swallowed and return ``None`` sink."""

    class _BrokenSink:
        def write(self, payload: str) -> None:  # pragma: no cover - invoked
            raise BrokenPipeError

        def flush(self) -> None:  # pragma: no cover - compatibility hook
            return None

    result = execution._write_to_sink(_BrokenSink(), "data")

    assert result is None


def test_echo_buffered_output_skips_empty_payloads() -> None:
    """The echo helper should not write anything for empty payloads."""
    sink = io.StringIO()
    execution._echo_buffered_output("", sink)
    assert sink.getvalue() == ""
