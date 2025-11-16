"""Tests covering logging for publish command execution helpers."""

from __future__ import annotations

import logging
import sys
import typing as typ

from lading.commands import publish, publish_execution
from lading.workspace import metadata as metadata_module

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from cmd_mox.controller import CmdMox

    LogCaptureFixture = pytest.LogCaptureFixture
    CaptureFixture = pytest.CaptureFixture
    MonkeyPatch = pytest.MonkeyPatch
else:  # pragma: no cover - typing helpers
    Path = typ.Any
    LogCaptureFixture = typ.Any
    CaptureFixture = typ.Any
    CmdMox = typ.Any
    MonkeyPatch = typ.Any


def test_invoke_logs_command_with_cwd(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    """``_invoke`` should log the command line and working directory."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    exit_code, stdout, stderr = publish._invoke(("echo", "hello"), cwd=tmp_path)

    assert exit_code == 0
    assert stdout.strip() == "hello"
    assert stderr == ""
    expected = f"Running external command: echo hello (cwd={tmp_path})"
    assert expected in caplog.messages


def test_invoke_logs_command_without_cwd(caplog: LogCaptureFixture) -> None:
    """``_invoke`` should omit ``cwd`` details when not provided."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")

    exit_code, stdout, stderr = publish._invoke(("echo", "hello"))

    assert exit_code == 0
    assert stdout.strip() == "hello"
    assert stderr == ""
    assert "Running external command: echo hello" in caplog.messages
    assert not any("(cwd=" in message for message in caplog.messages)


def test_invoke_proxies_command_output(capsys: CaptureFixture[str]) -> None:
    """``_invoke`` should stream stdout/stderr to the parent process."""
    script = """\
import sys
sys.stdout.write("alpha")
sys.stdout.flush()
sys.stderr.write("beta")
sys.stderr.flush()
"""

    exit_code, stdout, stderr = publish._invoke((sys.executable, "-c", script))

    assert exit_code == 0
    assert stdout == "alpha"
    assert stderr == "beta"
    captured = capsys.readouterr()
    assert captured.out == "alpha"
    assert captured.err == "beta"


def test_cmd_mox_passthrough_streams_output(
    cmd_mox: CmdMox,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """cmd-mox passthrough should stream via the subprocess runner."""
    monkeypatch.setenv(metadata_module.CMD_MOX_STUB_ENV_VAR, "1")
    script = "print('unused')"
    cmd_mox.spy(sys.executable).with_args("-c", script).passthrough()

    calls: list[tuple[str, tuple[str, ...], str | None]] = []

    def fake_invoke(
        program: str,
        args: tuple[str, ...],
        context: publish_execution._SubprocessContext,
    ) -> tuple[int, str, str]:
        calls.append((program, args, context.stdin_data))
        sys.stdout.write("alpha")
        sys.stdout.flush()
        sys.stderr.write("beta")
        sys.stderr.flush()
        return 0, "alpha", "beta"

    echo_payloads: list[str] = []

    def fake_echo(payload: str, sink: typ.TextIO) -> None:
        del sink
        echo_payloads.append(payload)

    monkeypatch.setattr(
        publish_execution,
        "_invoke_via_subprocess",
        fake_invoke,
    )
    monkeypatch.setattr(publish_execution, "_echo_buffered_output", fake_echo)

    exit_code, stdout, stderr = publish._invoke((sys.executable, "-c", script))

    assert exit_code == 0
    assert stdout == "alpha"
    assert stderr == "beta"
    captured = capsys.readouterr()
    assert captured.out == "alpha"
    assert captured.err == "beta"
    assert calls == [(sys.executable, ("-c", script), None)]
    assert not echo_payloads
