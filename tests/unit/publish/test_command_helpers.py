"""Unit tests for publish command execution helpers."""

import importlib
import io
from pathlib import Path

import pytest

from lading import cli
from lading.commands import publish, publish_execution
from lading.runtime import subprocess_runner
from lading.testing import cmd_mox_runner
from lading.testing.cmd_mox_runner import normalise_cmd_mox_command

execution = importlib.import_module("lading.runtime.subprocess_runner")


def test_normalise_environment_handles_none_and_values() -> None:
    """Environment normalisation should coerce values to strings."""
    assert execution.normalise_environment(None) is None
    assert execution.normalise_environment({"ALPHA": 1}) == {"ALPHA": "1"}


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

    execution.relay_stream(source, sink, buffer)

    assert buffer == ["alpha"]
    assert sink.getvalue() == "alpha"


def test_write_to_sink_handles_broken_pipe() -> None:
    """Broken pipe errors should be swallowed and return ``None`` sink."""

    class _BrokenSink:
        def write(self, payload: str) -> None:  # pragma: no cover - invoked
            raise BrokenPipeError

        def flush(self) -> None:  # pragma: no cover - compatibility hook
            return None

    result = execution.write_to_sink(_BrokenSink(), "data")

    assert result is None


def test_echo_buffered_output_skips_empty_payloads() -> None:
    """The echo helper should not write anything for empty payloads."""
    sink = io.StringIO()
    cmd_mox_runner._echo_buffered_output("", sink)
    assert sink.getvalue() == ""


def test_split_command_rejects_empty_sequence() -> None:
    """Splitting an empty command raises a descriptive error."""
    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_execution.split_command(())

    assert "Command sequence must contain" in str(excinfo.value)


@pytest.mark.parametrize(
    "command",
    [
        ("cargo", "check"),
        ("cargo", "test", "--workspace"),
        ("git", "status", "--porcelain"),
    ],
)
def test_normalise_cmd_mox_command_forwards_non_cargo_commands(
    command: tuple[str, ...],
) -> None:
    """cmd-mox normalisation preserves non-cargo commands and arguments."""
    program, args = command[0], tuple(command[1:])

    rewritten_program, rewritten_args = normalise_cmd_mox_command(program, args)

    if program == "cargo" and args:
        expected_program = f"cargo::{args[0]}"
        expected_args = list(args[1:])
    else:
        expected_program = program
        expected_args = list(args)

    assert rewritten_program == expected_program
    assert rewritten_args == expected_args


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on"])
def test_should_use_cmd_mox_stub_honours_truthy_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment values recognised as truthy enable cmd-mox stubbing."""
    monkeypatch.setenv(cli._CMD_MOX_STUB_ENV, value)

    assert cli._select_runner() is cmd_mox_runner.cmd_mox_runner


def test_should_use_cmd_mox_stub_returns_false_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing environment values disable cmd-mox stubbing."""
    monkeypatch.delenv(cli._CMD_MOX_STUB_ENV, raising=False)

    assert cli._select_runner() is subprocess_runner
