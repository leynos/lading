"""Tests for compiletest diagnostics helpers."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from lading.commands import publish_diagnostics

if typ.TYPE_CHECKING:
    import pytest


def test_append_compiletest_diagnostics_includes_tail_lines(tmp_path: Path) -> None:
    """When artifacts exist, the tail of the file should be appended."""
    artifact = tmp_path / "ui.stderr"
    artifact.write_text("line1\nline2\n", encoding="utf-8")

    message = publish_diagnostics._append_compiletest_diagnostics(
        "Pre-flight failed",
        stdout=str(artifact),
        stderr="",
        tail_lines=1,
    )

    assert "Compiletest stderr artifacts" in message
    assert "ui.stderr" in message
    assert "line2" in message


def test_append_compiletest_diagnostics_handles_missing_artifact(
    tmp_path: Path,
) -> None:
    """Missing artifacts should still be reported without raising."""
    artifact = tmp_path / "missing.stderr"

    message = publish_diagnostics._append_compiletest_diagnostics(
        "Failure",
        stdout=str(artifact),
        stderr="",
        tail_lines=2,
    )

    assert "(file not found)" in message


def test_append_compiletest_diagnostics_no_matches_returns_message() -> None:
    """When no artifacts are present the original message should be returned."""
    message = publish_diagnostics._append_compiletest_diagnostics(
        "Failure", stdout="", stderr="", tail_lines=2
    )

    assert message == "Failure"


def test_append_compiletest_diagnostics_deduplicates_artifacts(tmp_path: Path) -> None:
    """Duplicate artifact tokens should only be reported once."""
    artifact = tmp_path / "dupe.stderr"
    artifact.write_text("line\n", encoding="utf-8")
    stdout = f"{artifact} {artifact})"

    message = publish_diagnostics._append_compiletest_diagnostics(
        "Failure", stdout=stdout, stderr="", tail_lines=1
    )

    assert message.count("dupe.stderr") == 1


def test_read_tail_lines_handles_zero_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tail helper should handle zero counts and read failures."""
    bogus_path = Path("/nonexistent/nowhere.stderr")
    assert publish_diagnostics._read_tail_lines(bogus_path, 0) == ()

    def _raise(*args: object, **kwargs: object) -> str:
        message = "boom"
        raise OSError(message)

    monkeypatch.setattr(Path, "read_text", _raise)
    assert publish_diagnostics._read_tail_lines(bogus_path, 2) == ()


def test_format_artifact_diagnostics_when_no_tail(tmp_path: Path) -> None:
    """Artifacts without content should still list the path."""
    artifact = tmp_path / "empty.stderr"
    artifact.write_text("", encoding="utf-8")

    lines = publish_diagnostics._format_artifact_diagnostics(artifact, tail_lines=2)

    assert lines == [f"- {artifact}"]
