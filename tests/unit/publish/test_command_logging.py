"""Tests covering logging for publish command execution helpers."""

from __future__ import annotations

import logging
import typing as typ

from lading.commands import publish

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

    LogCaptureFixture = pytest.LogCaptureFixture
else:  # pragma: no cover - typing helpers
    Path = typ.Any
    LogCaptureFixture = typ.Any


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
