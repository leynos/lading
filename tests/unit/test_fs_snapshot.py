"""Unit tests for the non-following snapshot helper.

The helper must observe without following links and must not turn an unrelated
I/O failure into an empty subtree, which would make a no-write assertion pass
vacuously.
"""

import collections.abc as cabc
import errno
import os
import typing as typ

import pytest

from tests.helpers.fs_snapshot import changed_paths, snapshot_tree

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_snapshot_propagates_a_permission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission failure is preserved rather than reported as an empty list."""
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "file").write_text("content", encoding="utf-8")
    target = tmp_path.resolve() / "dir"
    real_scandir = os.scandir

    def denying_scandir(
        path: str | os.PathLike[str],
    ) -> cabc.Iterator[os.DirEntry[str]]:
        """Deny only the directory whose listing the test withholds."""
        if os.fspath(path) == str(target):
            raise PermissionError(errno.EACCES, "denied", str(target))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", denying_scandir)

    with pytest.raises(PermissionError):
        snapshot_tree(tmp_path)


def test_snapshot_propagates_an_unrelated_examination_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EIO-style failure is not converted into an empty listing."""
    (tmp_path / "dir").mkdir()
    target = tmp_path.resolve() / "dir"
    real_scandir = os.scandir

    def failing_scandir(
        path: str | os.PathLike[str],
    ) -> cabc.Iterator[os.DirEntry[str]]:
        """Fail with a non-permission error for the target directory."""
        if os.fspath(path) == str(target):
            raise OSError(errno.EIO, "I/O error", str(target))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)

    with pytest.raises(OSError, match="I/O error") as excinfo:
        snapshot_tree(tmp_path)

    assert excinfo.value.errno == errno.EIO, (
        "the original examination error must propagate unchanged"
    )


def test_snapshot_preserves_the_raw_symlink_target(tmp_path: Path) -> None:
    """A target spelling change is visible even when the resolved path is equal."""
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to("target/./file")
    before = snapshot_tree(tmp_path)

    link.unlink()
    link.symlink_to("target/file")
    after = snapshot_tree(tmp_path)

    before_target = next(
        entry.link_target for entry in before if entry.relative == "link"
    )
    after_target = next(
        entry.link_target for entry in after if entry.relative == "link"
    )
    assert (before_target, after_target) == ("target/./file", "target/file"), (
        "the snapshot must record the raw link text rather than a normalized path"
    )


def test_snapshot_classifies_the_root_symlink_target(tmp_path: Path) -> None:
    """Relinking the snapshotted root changes the snapshot."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    link = tmp_path / "link"
    link.symlink_to(first, target_is_directory=True)
    before = snapshot_tree(link)

    link.unlink()
    link.symlink_to(second, target_is_directory=True)
    after = snapshot_tree(link)

    assert changed_paths(before, after) == (".",), (
        "a different root link target must be observed rather than compared equal"
    )
