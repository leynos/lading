"""Unit tests for :mod:`lading.utils.process`."""

from __future__ import annotations

import logging
import typing as typ

from lading.utils import process

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

    LogCaptureFixture = pytest.LogCaptureFixture
else:  # pragma: no cover - typing helpers
    Path = typ.Any
    LogCaptureFixture = typ.Any


def test_format_command_renders_shell_representation() -> None:
    """Commands should be rendered using shell quoting rules."""
    rendered = process.format_command(("echo", "hello world"))

    assert rendered == "echo 'hello world'"


def test_format_command_warns_on_empty_command(
    caplog: LogCaptureFixture,
) -> None:
    """An empty command should emit a warning and return an empty string."""
    caplog.set_level(logging.WARNING, logger="lading.utils.process")

    rendered = process.format_command(())

    assert rendered == ""
    assert "empty command sequence" in caplog.text


def test_log_command_invocation_includes_cwd(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    """``log_command_invocation`` should render the working directory."""
    logger = logging.getLogger("tests.utils.process")
    caplog.set_level(logging.INFO, logger="tests.utils.process")

    process.log_command_invocation(logger, ("echo", "hello"), tmp_path)

    assert "Running external command: echo hello (cwd=" in caplog.text


def test_log_command_invocation_omits_cwd_when_absent(
    caplog: LogCaptureFixture,
) -> None:
    """The working directory should be omitted when ``cwd`` is ``None``."""
    logger = logging.getLogger("tests.utils.process")
    caplog.set_level(logging.INFO, logger="tests.utils.process")

    process.log_command_invocation(logger, ("echo", "hello"), None)

    assert "Running external command: echo hello" in caplog.messages
    assert not any("(cwd=" in message for message in caplog.messages)


def test_log_command_invocation_flags_empty_command() -> None:
    """Empty commands should log a warning and a placeholder message."""
    logger = logging.getLogger("tests.utils.process")
    logger.setLevel(logging.INFO)
    warning_logger = logging.getLogger("lading.utils.process")
    warning_logger.setLevel(logging.WARNING)

    class _RecordingHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    info_handler = _RecordingHandler()
    warning_handler = _RecordingHandler()
    logger.addHandler(info_handler)
    warning_logger.addHandler(warning_handler)
    try:
        process.log_command_invocation(logger, (), None)
    finally:
        logger.removeHandler(info_handler)
        warning_logger.removeHandler(warning_handler)

    assert any(
        record.levelno == logging.INFO
        and record.getMessage() == "Running external command: <empty command>"
        for record in info_handler.records
    )
    assert any(
        record.levelno == logging.WARNING
        and "empty command sequence" in record.getMessage()
        for record in warning_handler.records
    )
