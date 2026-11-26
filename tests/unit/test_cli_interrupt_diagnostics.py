"""Operator diagnostics for command interruptions."""

import collections.abc as cabc
from pathlib import Path

import pytest

from lading import cli


@pytest.mark.usefixtures("minimal_config")
def test_main_prints_keyboard_interrupt_recovery_notes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Show recovery details attached to an interruption to the operator."""
    interruption = KeyboardInterrupt()
    interruption.add_note("Could not inspect fixture Cargo.toml during recovery")

    def interrupt(_: cabc.Sequence[str]) -> int:
        """Raise the prepared interruption in place of dispatching the command."""
        raise interruption

    monkeypatch.setattr(cli, "_dispatch_and_print", interrupt)
    exit_code = cli.main(["bump", "1.2.3", "--workspace-root", str(tmp_path)])

    assert exit_code == 130, "interrupted commands must retain the conventional exit"
    captured = capsys.readouterr()
    assert "Operation cancelled by user." in captured.err, (
        "the CLI should retain its cancellation message"
    )
    assert "Could not inspect fixture Cargo.toml during recovery" in captured.err, (
        "the CLI should print recovery notes attached to the interruption"
    )


@pytest.mark.usefixtures("minimal_config")
def test_main_prints_unexpected_error_recovery_notes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Show recovery notes on the generic entry-point error path."""
    error = RuntimeError("fixture rewrite failed")
    error.add_note("Inspect the fixture before retrying")

    def fail(_: cabc.Sequence[str]) -> int:
        """Raise the prepared failure in place of dispatching the command."""
        raise error

    monkeypatch.setattr(cli, "_dispatch_and_print", fail)
    exit_code = cli.main(["bump", "1.2.3", "--workspace-root", str(tmp_path)])

    assert exit_code == 1, "unexpected command failures must retain exit status one"
    captured = capsys.readouterr()
    assert "Unexpected error: fixture rewrite failed" in captured.err, (
        "the CLI should retain the generic error message"
    )
    assert "Inspect the fixture before retrying" in captured.err, (
        "the CLI should print recovery notes attached to unexpected failures"
    )
