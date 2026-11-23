"""Unit tests for non-following confined glob expansion.

The glob suite is separate from the component-walk suite: expansion has its own
scan-and-match responsibility, and keeping it apart keeps both modules well
inside the file-size limit.
"""

import collections.abc as cabc
import errno
import os
import typing as typ

import pytest

from lading.utils.confinement import (
    BumpTargetExaminationError,
    GlobOptions,
    MatchExpectation,
    MissingBumpTargetError,
    UnmatchedPolicy,
    UnsafeBumpTargetError,
    expand_confined_glob,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

pytest_plugins = ("tests.unit.confinement_fixtures",)


def test_expand_confined_glob_returns_sorted_regular_files(tmp_path: Path) -> None:
    """Return sorted matches and ignore directory matches for file patterns."""
    root = tmp_path / "workspace"
    (root / "docs" / "nested").mkdir(parents=True)
    (root / "docs" / "b.md").write_text("", encoding="utf-8")
    (root / "docs" / "a.md").write_text("", encoding="utf-8")
    (root / "docs" / "nested" / "c.md").write_text("", encoding="utf-8")
    (root / "docs" / "not-markdown.txt").write_text("", encoding="utf-8")
    matches = expand_confined_glob(root, "docs/**/*.md", key="globs")
    assert [match.display for match in matches] == [
        "docs/a.md",
        "docs/b.md",
        "docs/nested/c.md",
    ], "matches must be sorted, recursive, and limited to regular Markdown files"


def test_expand_confined_glob_rejects_escaping_globs(tmp_path: Path) -> None:
    """Reject a documentation glob that traverses out of the workspace."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "README.md").write_text("external", encoding="utf-8")
    (root / "README.md").write_text("", encoding="utf-8")
    with pytest.raises(UnsafeBumpTargetError):
        expand_confined_glob(root, "../outside/README.md", key="globs")
    assert expand_confined_glob(root, "README.md", key="globs"), (
        "the valid workspace README must still expand"
    )


def test_expand_confined_glob_rejects_symlinked_matches(tmp_path: Path) -> None:
    """Reject a glob whose match is a symlink to an external file."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    outside.mkdir()
    (outside / "README.md").write_text("external", encoding="utf-8")
    (root / "docs" / "linked.md").symlink_to(outside / "README.md")
    (root / "docs" / "real.md").write_text("", encoding="utf-8")
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        expand_confined_glob(root, "docs/*.md", key="globs")
    assert "docs/linked.md" in str(excinfo.value), (
        "the diagnostic must name the symlinked match"
    )


def test_expand_confined_glob_ignores_links_on_the_way_to_a_match(
    tmp_path: Path,
) -> None:
    """Do not descend a symlinked directory, and do not fail on unrelated links."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    outside.mkdir()
    (outside / "evil.md").write_text("external", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("", encoding="utf-8")
    (root / "docs" / "image.png").symlink_to(outside / "evil.md")
    matches = expand_confined_glob(root, "docs/**/*.md", key="globs")
    assert [match.display for match in matches] == ["docs/guide.md"], (
        "a non-final symlink must contribute no match rather than failing the run"
    )


def test_expand_confined_glob_reports_special_files(tmp_path: Path) -> None:
    """Report a matched special file distinctly from a symlink."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    fifo = root / "docs" / "pipe.md"
    try:
        os.mkfifo(fifo)
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        expand_confined_glob(root, "docs/*.md", key="globs")
    assert "special file" in str(excinfo.value), (
        "a FIFO match must be reported as a special file"
    )


def test_expand_confined_glob_honours_the_unmatched_policy(tmp_path: Path) -> None:
    """Treat an unmatched glob as a no-op or an error according to policy."""
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("", encoding="utf-8")
    assert not expand_confined_glob(root, "docs/*.txt", key="globs"), (
        "an unmatched glob must be a no-op under the default policy"
    )
    with pytest.raises(MissingBumpTargetError) as excinfo:
        expand_confined_glob(
            root,
            "docs/*.txt",
            key="globs",
            options=GlobOptions(unmatched=UnmatchedPolicy.ERROR),
        )
    assert "docs/*.txt" in str(excinfo.value), (
        "the strict policy must name the unmatched pattern"
    )


def test_expand_confined_glob_selects_member_directories(tmp_path: Path) -> None:
    """Return member directories and ignore regular-file matches."""
    root = tmp_path / "workspace"
    (root / "crates" / "alpha").mkdir(parents=True)
    (root / "crates" / "beta").mkdir(parents=True)
    (root / "crates" / "stray-file").write_text("", encoding="utf-8")
    matches = expand_confined_glob(
        root,
        "crates/*",
        key="workspace.members",
        options=GlobOptions(
            expect=MatchExpectation.DIRECTORY,
            unmatched=UnmatchedPolicy.ERROR,
        ),
    )
    assert [match.display for match in matches] == ["crates/alpha", "crates/beta"], (
        "a member pattern must select directories and ignore stray files, as Cargo does"
    )


def test_expand_confined_glob_reports_scandir_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an unreadable directory as an examination failure."""
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("", encoding="utf-8")
    real_scandir = os.scandir
    target = root.resolve() / "docs"

    def failing_scandir(path: object) -> cabc.Iterator[os.DirEntry[str]]:
        """Fail only for the exact directory under scan."""
        if os.fspath(typ.cast("os.PathLike[str]", path)) == str(target):
            raise PermissionError(errno.EACCES, "denied", str(target))
        return real_scandir(typ.cast("os.PathLike[str]", path))

    monkeypatch.setattr(os, "scandir", failing_scandir)
    with pytest.raises(BumpTargetExaminationError) as excinfo:
        expand_confined_glob(root, "docs/*.md", key="globs")
    assert "cannot list directory" in str(excinfo.value), (
        "a scan failure must surface instead of looking like an empty match"
    )
