"""Unit tests for plan-builder queries and read failures."""

from pathlib import Path

import pytest

from lading.commands import bump_toml
from lading.commands.bump_manifest_rewrite_model import ManifestRewriteWriteError
from lading.commands.bump_plan import PlanBuilder, WriteCategory, commit_plan
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


def test_commit_plan_reports_write_progress_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed write names its destination and write progress."""
    root = tmp_path.resolve()
    first = root / "first.toml"
    second = root / "second.toml"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    builder = PlanBuilder(root=root)
    builder.propose(_confined(root, "first.toml"), "ONE\n", WriteCategory.MANIFEST)
    builder.propose(
        _confined(root, "second.toml"), "TWO\n", WriteCategory.DOCUMENTATION
    )
    plan = builder.build(target_version="2.0.0", lockfiles=None)
    original_write = bump_toml.write_atomic_text

    def fail_second_write(path: Path, content: str) -> None:
        """Reject only the documentation destination."""
        if path == second:
            message = "write rejected"
            raise OSError(message)
        original_write(path, content)

    monkeypatch.setattr(bump_toml, "write_atomic_text", fail_second_write)

    with pytest.raises(ManifestRewriteWriteError) as failure:
        commit_plan(plan)

    assert failure.value.completed_writes == 1, "one write completed before the failure"
    assert failure.value.planned_writes == 2, "the plan contained two writes"
    assert failure.value.failed_path == second, "the failed destination must be named"
    assert isinstance(failure.value.__cause__, OSError), (
        "the original write error must be chained"
    )
