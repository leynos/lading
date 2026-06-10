"""Unit tests for :mod:`lading.utils.process`."""

from __future__ import annotations

import logging
import typing as typ

import hypothesis.strategies as st
from hypothesis import given

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


# ---------------------------------------------------------------------------
# command_detail / with_detail (issue #102)
# ---------------------------------------------------------------------------

_output_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40
)


@given(stdout=_output_text, stderr=_output_text)
def test_command_detail_prefers_stderr_then_stdout(stdout: str, stderr: str) -> None:
    """Prefer stripped stderr; fall back to stripped stdout."""
    detail = process.command_detail(stdout, stderr)

    if stderr.strip():
        assert detail == stderr.strip()
    elif stdout.strip():
        assert detail == stdout.strip()
    else:
        assert detail == ""
    assert detail == detail.strip()


@given(detail=_output_text)
def test_append_detail_appends_only_when_detail_present(detail: str) -> None:
    """A pre-derived detail is appended verbatim only when it is non-empty."""
    message = "Build failed"
    rendered = process.append_detail(message, detail)

    if detail:
        assert rendered == f"{message}: {detail}"
    else:
        assert rendered == message


def test_append_detail_matches_with_detail() -> None:
    """``with_detail`` is the derive-then-append wrapper over ``append_detail``."""
    stdout, stderr = "  ", "boom\n"
    detail = process.command_detail(stdout, stderr)

    assert process.with_detail("Failed", stdout, stderr) == process.append_detail(
        "Failed", detail
    )


def test_append_detail_supports_custom_separator() -> None:
    """A custom separator joins the message and pre-derived detail."""
    assert process.append_detail("Failed", "boom", separator="; ") == "Failed; boom"


@given(stdout=_output_text, stderr=_output_text)
def test_with_detail_appends_only_when_detail_present(stdout: str, stderr: str) -> None:
    """The suffix appears exactly when stripped output exists."""
    message = "Build failed"
    rendered = process.with_detail(message, stdout, stderr)

    detail = process.command_detail(stdout, stderr)
    if detail:
        assert rendered == f"{message}: {detail}"
    else:
        assert rendered == message


def test_with_detail_supports_custom_separator() -> None:
    """A custom separator joins the message and detail."""
    rendered = process.with_detail("Failed", "", "boom", separator="; ")

    assert rendered == "Failed; boom"
