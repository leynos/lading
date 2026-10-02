"""Tests for compiletest diagnostics helpers."""

import typing as typ
from pathlib import Path

from lading.commands import publish_diagnostics
from tests.helpers.path_normalization import normalized

if typ.TYPE_CHECKING:
    import pytest
    from syrupy.assertion import SnapshotAssertion


def test_append_compiletest_diagnostics_includes_tail_lines(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """When artefacts exist, the tail of the file should be appended."""
    artefact = tmp_path / "ui.stderr"
    artefact.write_text("line1\nline2\n", encoding="utf-8")

    message = publish_diagnostics._append_compiletest_diagnostics(
        "Pre-flight failed",
        stdout=str(artefact),
        stderr="",
        tail_lines=1,
    )

    assert normalized(message, tmp_path) == snapshot, (
        "the assembled diagnostics must name the artefact and include its tail"
    )


def test_append_compiletest_diagnostics_handles_missing_artefact(
    tmp_path: Path,
) -> None:
    """Missing artefacts should still be reported without raising."""
    artefact = tmp_path / "missing.stderr"

    message = publish_diagnostics._append_compiletest_diagnostics(
        "Failure",
        stdout=str(artefact),
        stderr="",
        tail_lines=2,
    )

    assert "(file not found)" in message, (
        "a missing artefact must be reported inline in the diagnostics"
    )


def test_append_compiletest_diagnostics_no_matches_returns_message() -> None:
    """When no artefacts are present the original message should be returned."""
    message = publish_diagnostics._append_compiletest_diagnostics(
        "Failure", stdout="", stderr="", tail_lines=2
    )

    assert message == "Failure", (
        "the original message must be returned unchanged when no artefacts match"
    )


def test_append_compiletest_diagnostics_deduplicates_artefacts(tmp_path: Path) -> None:
    """Duplicate artefact tokens should only be reported once."""
    artefact = tmp_path / "dupe.stderr"
    artefact.write_text("line\n", encoding="utf-8")
    stdout = f"{artefact} {artefact})"

    message = publish_diagnostics._append_compiletest_diagnostics(
        "Failure", stdout=stdout, stderr="", tail_lines=1
    )

    assert message.count("dupe.stderr") == 1, (
        "a repeated artefact token must be reported only once"
    )


def test_read_tail_lines_handles_zero_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tail helper should handle zero counts and read failures."""
    bogus_path = Path("/nonexistent/nowhere.stderr")
    assert not publish_diagnostics._read_tail_lines(bogus_path, 0), (
        "a request for zero tail lines must yield no lines"
    )

    def _raise(*args: object, **kwargs: object) -> str:
        message = "boom"
        raise OSError(message)

    monkeypatch.setattr(Path, "read_text", _raise)
    assert not publish_diagnostics._read_tail_lines(bogus_path, 2), (
        "an unreadable artefact must yield no tail lines rather than raising"
    )


def test_format_artefact_diagnostics_when_no_tail(tmp_path: Path) -> None:
    """Artefacts without content should still list the path."""
    artefact = tmp_path / "empty.stderr"
    artefact.write_text("", encoding="utf-8")

    lines = publish_diagnostics._format_artefact_diagnostics(artefact, tail_lines=2)

    assert lines == [f"- {artefact}"], (
        "an empty artefact must still be listed with its path"
    )
