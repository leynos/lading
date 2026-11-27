"""Unit tests for plan-builder queries and read failures."""

from pathlib import Path

import pytest

from lading.commands import bump_toml
from lading.commands.bump_manifest_rewrite_model import ManifestRewriteWriteError
from lading.commands.bump_plan import (
    PlanBuilder,
    PlannedWrite,
    WriteCategory,
    commit_plan,
)
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


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("directory", "docs"), id="directory"),
        pytest.param(("undecodable", "bad.md"), id="undecodable"),
    ],
)
def test_read_reports_an_unusable_target(
    tmp_path: Path,
    case: tuple[str, str],
) -> None:
    """A directory or undecodable file named as a target is an examination error."""
    kind, relative = case
    root = tmp_path.resolve()
    target = root / relative
    if kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"\xff\xfe\x00")
    builder = PlanBuilder(root=root)

    # The path is built directly: this test targets the read, not the
    # confinement policy that would reject a directory first.
    with pytest.raises(BumpTargetExaminationError) as excinfo:
        builder.read(ConfinedPath(root=root, parts=(relative,)))

    assert "cannot read bump target" in str(excinfo.value), (
        "the diagnostic must name the unreadable bump target"
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


def test_commit_plan_reports_a_failed_attempt_to_the_observer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write observer sees the failing attempt as well as completed writes."""
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
    observed: list[tuple[str, float]] = []
    original_write = bump_toml.write_atomic_text

    def record(proposal: PlannedWrite, elapsed: float) -> None:
        """Record each observed attempt by destination name."""
        observed.append((proposal.path.path.name, elapsed))

    def fail_second_write(path: Path, content: str) -> None:
        """Reject only the documentation destination."""
        if path == second:
            message = "write rejected"
            raise OSError(message)
        original_write(path, content)

    monkeypatch.setattr(bump_toml, "write_atomic_text", fail_second_write)

    with pytest.raises(ManifestRewriteWriteError):
        commit_plan(plan, on_write=record)

    assert [name for name, _elapsed in observed] == ["first.toml", "second.toml"], (
        "the observer must see the completed write and the failed attempt once each"
    )
    assert all(elapsed >= 0.0 for _name, elapsed in observed), (
        "each observation must carry a non-negative elapsed time"
    )
