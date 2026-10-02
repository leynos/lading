"""Unit tests for :mod:`lading.utils.process`."""

import logging
import typing as typ

import hypothesis.strategies as st
from hypothesis import given

from lading.utils import process

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_format_command_renders_shell_representation() -> None:
    """Commands should be rendered using shell quoting rules."""
    rendered = process.format_command(("echo", "hello world"))

    assert rendered == "echo 'hello world'", (
        "an argument containing a space must be single-quoted"
    )


def test_format_command_warns_on_empty_command(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty command should emit a warning and return an empty string."""
    caplog.set_level(logging.WARNING, logger="lading.utils.process")

    rendered = process.format_command(())

    assert not rendered, "an empty command must render as the empty string"
    assert "empty command sequence" in caplog.text, (
        "an empty command must warn; it is almost always a caller bug"
    )


def test_log_command_invocation_includes_cwd(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``log_command_invocation`` should render the working directory."""
    logger = logging.getLogger("tests.utils.process")
    caplog.set_level(logging.INFO, logger="tests.utils.process")

    process.log_command_invocation(logger, ("echo", "hello"), tmp_path)

    assert "Running external command: echo hello (cwd=" in caplog.text, (
        "a supplied cwd must be rendered into the log message"
    )


def test_log_command_invocation_omits_cwd_when_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The working directory should be omitted when ``cwd`` is ``None``."""
    logger = logging.getLogger("tests.utils.process")
    caplog.set_level(logging.INFO, logger="tests.utils.process")

    process.log_command_invocation(logger, ("echo", "hello"), None)

    assert "Running external command: echo hello" in caplog.messages, (
        "the command must be logged even when no cwd is supplied"
    )
    assert not any("(cwd=" in message for message in caplog.messages), (
        "no cwd segment may appear when cwd is None"
    )


def test_log_command_invocation_flags_empty_command(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty commands should log a warning and a placeholder message."""
    # ``set_level`` sets the shared capture handler's level as well as the
    # named logger's, so both levels must be INFO: a WARNING call here would
    # raise the handler above the INFO record the assertion below needs.
    caplog.set_level(logging.INFO, logger="tests.utils.process")
    caplog.set_level(logging.INFO, logger="lading.utils.process")
    logger = logging.getLogger("tests.utils.process")

    process.log_command_invocation(logger, (), None)

    # The two messages come from different loggers, so assert on each separately
    # rather than on the merged text: the placeholder is the caller's, the
    # warning is the module's, and swapping them would be a real defect.
    info_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "tests.utils.process" and record.levelno == logging.INFO
    ]
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "lading.utils.process" and record.levelno == logging.WARNING
    ]
    assert info_messages == ["Running external command: <empty command>"], (
        "the caller's logger must receive one INFO placeholder for the empty command"
    )
    assert any("empty command sequence" in message for message in warning_messages), (
        "the module logger must warn that the command sequence is empty"
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
        assert detail == stderr.strip(), (
            "non-blank stderr is the preferred source, stripped of whitespace"
        )
    elif stdout.strip():
        assert detail == stdout.strip(), (
            "stdout is the fallback only when stderr strips to nothing"
        )
    else:
        assert not detail, "both streams blank must yield no detail at all"
    assert detail == detail.strip(), "the detail must always be stripped"


@given(detail=_output_text)
def test_append_detail_appends_only_when_detail_present(detail: str) -> None:
    """A pre-derived detail is appended verbatim only when it is non-empty."""
    message = "Build failed"
    rendered = process.append_detail(message, detail)

    if detail:
        assert rendered == f"{message}: {detail}", (
            "a non-empty detail must be appended after the default separator"
        )
    else:
        assert rendered == message, (
            "an empty detail must leave the message untouched, with no separator"
        )


def test_append_detail_matches_with_detail() -> None:
    """``with_detail`` is the derive-then-append wrapper over ``append_detail``."""
    stdout, stderr = "  ", "boom\n"
    detail = process.command_detail(stdout, stderr)

    assert process.with_detail("Failed", stdout, stderr) == process.append_detail(
        "Failed", detail
    ), "with_detail must not diverge from deriving then appending"


def test_append_detail_supports_custom_separator() -> None:
    """A custom separator joins the message and pre-derived detail."""
    assert process.append_detail("Failed", "boom", separator="; ") == "Failed; boom", (
        "the caller's separator must replace the default colon"
    )


@given(stdout=_output_text, stderr=_output_text)
def test_with_detail_appends_only_when_detail_present(stdout: str, stderr: str) -> None:
    """The suffix appears exactly when stripped output exists."""
    message = "Build failed"
    rendered = process.with_detail(message, stdout, stderr)

    detail = process.command_detail(stdout, stderr)
    if detail:
        assert rendered == f"{message}: {detail}", (
            "the derived detail must be appended to the message"
        )
    else:
        assert rendered == message, (
            "blank streams must leave the message untouched, with no separator"
        )


def test_with_detail_supports_custom_separator() -> None:
    """A custom separator joins the message and detail."""
    rendered = process.with_detail("Failed", "", "boom", separator="; ")

    assert rendered == "Failed; boom", (
        "with_detail must pass the caller's separator through to append_detail"
    )


def test_c_locale_env_pins_locale_over_supplied_env() -> None:
    """Locale variables are pinned while other supplied values survive."""
    merged = process.c_locale_env({"CARGO_TERM_COLOR": "never", "LC_ALL": "fr_FR"})

    assert merged["CARGO_TERM_COLOR"] == "never", "caller values must survive"
    assert merged["LC_ALL"] == "C", "LC_ALL must be pinned to C"
    assert merged["LANG"] == "C", "LANG must be pinned to C"
    assert not merged["LANGUAGE"], (
        "LANGUAGE must be cleared; a non-empty value overrides LC_ALL for "
        "message translation"
    )


def test_c_locale_env_defaults_to_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``None`` base inherits the process environment before pinning."""
    monkeypatch.setenv("LADING_TEST_MARKER", "present")
    monkeypatch.setenv("LANGUAGE", "fr")

    merged = process.c_locale_env()

    assert merged["LADING_TEST_MARKER"] == "present", (
        "None must inherit the current process environment"
    )
    assert not merged["LANGUAGE"], "inherited LANGUAGE must still be cleared"
