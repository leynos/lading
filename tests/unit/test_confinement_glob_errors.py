"""Unit tests for glob expansion internals and rejection branches."""

import errno
import os
import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import (
    BumpTargetExaminationError,
    UnsafeBumpTargetError,
    expand_confined_glob,
    is_link_like,
)
from lading.utils.confinement.glob import (
    GlobOptions,
    MatchExpectation,
    UnmatchedPolicy,
    _Expansion,
)


def _expansion(root: Path) -> _Expansion:
    """Return an expansion with documentation defaults."""
    return _Expansion(
        root=root,
        key="globs",
        expect=MatchExpectation.REGULAR_FILE,
        unmatched=UnmatchedPolicy.IGNORE,
        link_predicate=is_link_like,
    )


def test_scandir_returns_none_for_a_missing_directory(tmp_path: Path) -> None:
    """A directory that vanished produces no entries rather than an error."""
    assert _expansion(tmp_path)._scandir(tmp_path / "missing") is None, (
        "a missing directory must be reported as absent"
    )


def test_iter_entries_yields_nothing_for_a_missing_directory(tmp_path: Path) -> None:
    """The entry iterator tolerates a missing starting directory."""
    entries = list(_expansion(tmp_path)._iter_entries(("missing",), "*.md"))

    assert not entries, "no entries can be produced from a missing directory"


def test_entry_stat_reports_an_examination_failure(tmp_path: Path) -> None:
    """A failing entry stat surfaces as an examination error."""

    class _FailingEntry:
        """Minimal DirEntry stand-in whose stat fails."""

        path = str(tmp_path / "entry")

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            """Raise the injected failure."""
            raise PermissionError(errno.EACCES, "denied", self.path)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        _expansion(tmp_path)._entry_stat(typ.cast("os.DirEntry[str]", _FailingEntry()))

    assert "cannot examine match" in str(excinfo.value), (
        "the diagnostic must name the unreadable match"
    )


def test_literal_expansion_reports_an_examination_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An lstat failure on a literal component is an examination error."""
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "README.md"
    real_lstat = os.lstat

    def failing_lstat(path: str | os.PathLike[str]) -> os.stat_result:
        """Fail only for the documentation target."""
        if os.fspath(path) == str(target):
            raise PermissionError(errno.EACCES, "denied", str(target))
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", failing_lstat)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        expand_confined_glob(
            root,
            "README.md",
            key="globs",
            options=GlobOptions(unmatched=UnmatchedPolicy.IGNORE),
        )

    assert "cannot examine target" in str(excinfo.value), (
        "the diagnostic must name the unreadable target"
    )


def test_a_final_recursive_glob_selects_regular_files(tmp_path: Path) -> None:
    """A pattern ending in ``**`` returns every regular file recursively."""
    root = tmp_path / "workspace"
    (root / "docs" / "nested").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("", encoding="utf-8")
    (root / "docs" / "nested" / "b.md").write_text("", encoding="utf-8")

    matches = expand_confined_glob(root, "docs/**", key="globs")

    assert [match.display for match in matches] == [
        "docs/a.md",
        "docs/nested/b.md",
    ], "a final recursive glob must return the files beneath the directory"


def test_a_final_recursive_glob_rejects_a_symlink(tmp_path: Path) -> None:
    """A symlink matched by a final ``**`` is rejected."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    outside.mkdir()
    (outside / "evil.md").write_text("external", encoding="utf-8")
    (root / "docs" / "link").symlink_to(outside / "evil.md")

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        expand_confined_glob(root, "docs/**", key="globs")

    assert "symlink" in str(excinfo.value), (
        "a final recursive glob must reject a symlink match"
    )


def test_a_root_level_recursive_glob_matches_files(tmp_path: Path) -> None:
    """A bare ``**`` applies the final expectation to the root itself."""
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("", encoding="utf-8")

    matches = expand_confined_glob(root, "**", key="globs")

    assert [match.display for match in matches] == ["README.md", "docs/guide.md"], (
        "a root-level recursive glob must return files at every depth"
    )


def test_recursive_glob_reports_an_unreadable_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure examining the ``**`` starting directory is an examination error."""
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "docs"
    target.mkdir()
    real_lstat = os.lstat
    seen: list[str] = []

    def failing_lstat(path: str | os.PathLike[str]) -> os.stat_result:
        """Fail only on the second examination of the matched directory."""
        if os.fspath(path) == str(target):
            seen.append(str(path))
            if len(seen) == 2:
                message = "denied"
                raise PermissionError(errno.EACCES, message, str(path))
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", failing_lstat)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        expand_confined_glob(root, "docs/**", key="globs")

    assert "cannot examine target" in str(excinfo.value), (
        "the diagnostic must name the unreadable starting directory"
    )
