"""Unit tests for plan-builder queries and read failures."""

from pathlib import Path

import pytest

from lading.commands.bump_plan import PlanBuilder, WriteCategory
from lading.utils.confinement import (
    BumpTargetExaminationError,
    ConfinedPath,
    ConfineOptions,
    confine_relative_path,
)


def _confined(root: Path, relative: str) -> ConfinedPath:
    """Return a confined may-create path for ``relative`` beneath ``root``."""
    return confine_relative_path(
        root,
        relative,
        key="test",
        options=ConfineOptions(may_create=True),
    )


def test_has_changes_reports_the_recorded_categories(tmp_path: Path) -> None:
    """Only a category that produced a proposal is reported as changed."""
    (tmp_path / "README.md").write_text("initial\n", encoding="utf-8")
    root = tmp_path.resolve()
    builder = PlanBuilder(root=root)
    assert not builder.has_changes(WriteCategory.DOCUMENTATION), (
        "an empty builder must report no changes"
    )

    builder.propose(
        _confined(root, "README.md"), "changed\n", WriteCategory.DOCUMENTATION
    )

    assert builder.has_changes(WriteCategory.DOCUMENTATION), (
        "the proposed documentation category must be reported"
    )
    assert not builder.has_changes(WriteCategory.MANIFEST), (
        "an unrelated category must not be reported"
    )


def test_read_returns_none_for_a_missing_target(tmp_path: Path) -> None:
    """A file that does not exist has no current content."""
    root = tmp_path.resolve()
    builder = PlanBuilder(root=root)

    assert builder.read(_confined(root, "missing.md")) is None, (
        "an absent target must report no content rather than an error"
    )


def test_read_reports_a_directory_target(tmp_path: Path) -> None:
    """A directory named as a file is an examination error."""
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    builder = PlanBuilder(root=root)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        builder.read(ConfinedPath(root=root, parts=("docs",)))

    assert "cannot read bump target" in str(excinfo.value), (
        "the diagnostic must name the unreadable bump target"
    )


def test_read_reports_undecodable_content(tmp_path: Path) -> None:
    """A target that is not valid UTF-8 is an examination error."""
    root = tmp_path.resolve()
    (root / "bad.md").write_bytes(b"\xff\xfe\x00")
    builder = PlanBuilder(root=root)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        builder.read(_confined(root, "bad.md"))

    assert "cannot read bump target" in str(excinfo.value), (
        "the diagnostic must name the undecodable bump target"
    )
