"""Regression tests for subprocess runner invocation logging.

Issue #104: every external command used to be logged twice — once at INFO
by ``subprocess_runner`` and once at DEBUG by ``invoke_via_subprocess``.
These tests pin the single-log contract.
"""

from __future__ import annotations

import logging
import typing as typ

from lading.runtime.subprocess_runner import subprocess_runner

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

    LogCaptureFixture = pytest.LogCaptureFixture
else:  # pragma: no cover - typing helpers
    Path = typ.Any
    LogCaptureFixture = typ.Any

_RUNNER_LOGGER = "lading.runtime.subprocess_runner"


def _invocation_records(
    caplog: LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return records that render the external command line."""
    return [
        record
        for record in caplog.records
        if "Running external command" in record.getMessage()
    ]


def _assert_no_spawn_record(caplog: LogCaptureFixture) -> None:
    """Assert the removed DEBUG spawn log is absent (regression for #104)."""
    assert all(
        "Spawning subprocess:" not in record.getMessage() for record in caplog.records
    )


def test_command_logged_exactly_once(caplog: LogCaptureFixture) -> None:
    """A command produces a single invocation log record at INFO."""
    caplog.set_level(logging.DEBUG, logger=_RUNNER_LOGGER)

    exit_code, stdout, stderr = subprocess_runner(("echo", "hello"), echo_stdout=False)

    assert exit_code == 0
    assert stdout.strip() == "hello"
    assert stderr == ""
    records = _invocation_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert "Running external command" in message
    assert "echo hello" in message
    _assert_no_spawn_record(caplog)


def test_command_logged_exactly_once_with_cwd(
    caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    """The single invocation record includes the working directory."""
    caplog.set_level(logging.DEBUG, logger=_RUNNER_LOGGER)

    exit_code, _, _ = subprocess_runner(
        ("echo", "hello"), cwd=tmp_path, echo_stdout=False
    )

    assert exit_code == 0
    records = _invocation_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert f"(cwd={tmp_path})" in records[0].getMessage()
    _assert_no_spawn_record(caplog)
