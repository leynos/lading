"""Exercise temporary-file naming and cleanup in atomic path writes."""

import collections.abc as cabc
import functools
import os
import stat
from pathlib import Path

import pytest

from lading.commands import bump_toml_files


class _ReadOnlyTemporaryFilesystem:
    """Simulate Windows-style refusal to remove read-only temporaries."""

    def __init__(
        self,
        original_unlink: cabc.Callable[..., None],
        original_chmod: cabc.Callable[..., None],
        readonly_error_message: str,
    ) -> None:
        """Keep original operations and state for the simulated filesystem."""
        self.original_unlink = original_unlink
        self.original_chmod = original_chmod
        self.readonly_error_message = readonly_error_message
        self.chmodded_temporaries: set[Path] = set()
        self.unlink_attempts: list[tuple[Path, bool]] = []

    def __get__(
        self,
        instance: Path | None,
        _owner: type[Path],
    ) -> cabc.Callable[..., None] | _ReadOnlyTemporaryFilesystem:
        """Bind a Path instance to the simulated unlink operation."""
        if instance is None:
            return self
        return functools.partial(self.unlink, instance)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        """Reject unlink until the temporary has its owner-write bit."""
        if path.name.startswith(".lading-"):
            self._record_unlink_attempt(path)
        self.original_unlink(path, missing_ok=missing_ok)

    def chmod(
        self,
        path: Path,
        mode: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        """Track owner writability before forwarding the requested mode."""
        if path.name.startswith(".lading-"):
            self._record_writable_mode(path, mode)
        self.original_chmod(path, mode, follow_symlinks=follow_symlinks)

    def _record_unlink_attempt(self, path: Path) -> None:
        """Capture a temporary unlink and enforce its simulated mode."""
        is_writable = path in self.chmodded_temporaries
        self.unlink_attempts.append((path, is_writable))
        if not is_writable:
            raise PermissionError(self.readonly_error_message)

    def _record_writable_mode(self, path: Path, mode: int) -> None:
        """Track the owner-write bit supplied to chmod."""
        if mode & stat.S_IWUSR:
            self.chmodded_temporaries.add(path)
        else:
            self.chmodded_temporaries.discard(path)


def test_path_fallback_uses_short_temporary_name_for_long_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporary prefixes fit beside destination names near the path limit."""
    destination = tmp_path / ("m" * 245)
    destination.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_stable_temporary_cleanup",
        lambda: False,
    )

    bump_toml_files.write_atomic_text(destination, "replacement")

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "path-based writes must not derive the temporary name from the destination"
    )
    assert not list(tmp_path.glob(".lading-*")), (
        "successful path-based replacement must leave no temporary file"
    )


def test_bounded_writer_uses_short_temporary_name_for_long_destination(
    tmp_path: Path,
) -> None:
    """Descriptor-relative temporary names also ignore the destination name."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    destination = tmp_path / ("m" * 245)
    destination.write_text("original", encoding="utf-8")

    bump_toml_files.write_atomic_text(
        destination,
        "replacement",
        workspace_root=tmp_path,
    )

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "bounded replacement must not exceed the filesystem's basename limit"
    )
    assert not list(tmp_path.glob(".lading-*")), (
        "bounded replacement must leave no temporary file"
    )


def test_path_fallback_cleans_temp_from_parent_moved_during_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup uses the pinned directory after the parent path is redirected."""
    if not bump_toml_files._supports_stable_temporary_cleanup():
        pytest.skip("stable directory-relative cleanup is unavailable on this platform")
    workspace_root = tmp_path / "workspace"
    parent = workspace_root / "fixtures"
    parent.mkdir(parents=True)
    manifest = parent / "Cargo.toml"
    manifest.write_text("original", encoding="utf-8")
    moved_parent = workspace_root / "moved-fixtures"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Cargo.toml").write_text("outside", encoding="utf-8")
    outside_original = (outside / "Cargo.toml").read_bytes()
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )
    resolve_target = bump_toml_files._resolve_bounded_write_target
    validation_count = 0

    def move_parent_before_final_check(path: Path, root: Path) -> Path:
        """Move the parent after temp creation, then run normal containment."""
        nonlocal validation_count
        validation_count += 1
        if validation_count == 2:
            parent.rename(moved_parent)
            parent.symlink_to(outside, target_is_directory=True)
        return resolve_target(path, root)

    monkeypatch.setattr(
        bump_toml_files,
        "_resolve_bounded_write_target",
        move_parent_before_final_check,
    )

    with pytest.raises(PermissionError, match="within the workspace"):
        bump_toml_files.write_atomic_text(
            manifest,
            "replacement",
            workspace_root=workspace_root,
        )

    assert validation_count == 2, "the final check must run after temp creation"
    assert not list(moved_parent.glob(".lading-*")), (
        "cleanup must unlink from the directory that created the temporary file"
    )
    assert (moved_parent / "Cargo.toml").read_text(encoding="utf-8") == "original", (
        "a failed final check must leave the moved destination unchanged"
    )
    assert (outside / "Cargo.toml").read_bytes() == outside_original, (
        "a failed final check must leave the redirected outside file unchanged"
    )


def test_path_fallback_reports_cleanup_failure_without_masking_write_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path cleanup failures are notes on the original replacement error."""
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_stable_temporary_cleanup",
        lambda: False,
    )
    write_error_message = "write denied"

    def reject_target(_path: Path, _root: Path) -> Path:
        """Fail validation after the temporary file has been written."""
        raise PermissionError(write_error_message)

    monkeypatch.setattr(bump_toml_files, "_resolve_bounded_write_target", reject_target)
    original_unlink = Path.unlink
    cleanup_error_message = "cleanup denied"

    def reject_temporary_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        """Fail only for the temporary file so the primary error remains visible."""
        if path.name.startswith(".lading-"):
            raise PermissionError(cleanup_error_message)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", reject_temporary_cleanup)

    with pytest.raises(PermissionError, match="write denied") as failure:
        bump_toml_files._write_path_based_atomic_text(
            destination,
            "replacement",
            workspace_root=tmp_path,
        )

    assert any("cleanup denied" in note for note in failure.value.__notes__), (
        "cleanup failure details must be retained as a note on the write failure"
    )
    assert destination.read_text(encoding="utf-8") == "original", (
        "cleanup failure must not alter the destination"
    )


def test_path_fallback_retries_cleanup_after_readonly_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only temporary can be made removable after replacement fails."""
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_stable_temporary_cleanup",
        lambda: False,
    )
    replacement_error_message = "replacement rejected"

    def reject_replacement(_path: Path, _root: Path) -> Path:
        """Fail after the temporary file has been written."""
        raise PermissionError(replacement_error_message)

    monkeypatch.setattr(
        bump_toml_files, "_resolve_bounded_write_target", reject_replacement
    )
    readonly_error_message = "read-only temporary"
    readonly_filesystem = _ReadOnlyTemporaryFilesystem(
        Path.unlink,
        os.chmod,
        readonly_error_message,
    )
    monkeypatch.setattr(Path, "unlink", readonly_filesystem)
    monkeypatch.setattr(os, "chmod", readonly_filesystem.chmod)

    with pytest.raises(PermissionError, match=replacement_error_message):
        bump_toml_files._write_path_based_atomic_text(
            destination,
            "replacement",
            workspace_root=tmp_path,
        )

    assert len(readonly_filesystem.unlink_attempts) == 2, (
        "cleanup should retry unlink after making the temporary writable"
    )
    assert [
        is_writable for _path, is_writable in readonly_filesystem.unlink_attempts
    ] == [False, True], "the retry must follow a writable-mode change"
    assert not list(tmp_path.glob(".lading-*")), (
        "the writable retry should remove the temporary file"
    )
    assert destination.read_text(encoding="utf-8") == "original", (
        "a failed replacement must preserve the original destination"
    )


def test_bounded_writer_keeps_write_error_when_temporary_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Descriptor-relative cleanup errors do not replace the write failure."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")
    write_error_message = "mode update rejected"

    def reject_mode_update(_descriptor: int, _mode: int) -> None:
        """Fail after temporary creation so descriptor cleanup is exercised."""
        raise OSError(write_error_message)

    monkeypatch.setattr(bump_toml_files, "_set_file_mode", reject_mode_update)
    original_unlink = os.unlink
    cleanup_error_message = "cleanup denied"

    def reject_temporary_unlink(
        path: str | Path,
        *,
        dir_fd: int | None = None,
    ) -> None:
        """Reject cleanup only for the writer's uniquely named temporary."""
        if Path(path).name.startswith(".lading-"):
            raise PermissionError(cleanup_error_message)
        if dir_fd is None:
            original_unlink(path)
        else:
            original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", reject_temporary_unlink)
    monkeypatch.setattr(
        bump_toml_files, "_supports_bounded_directory_operations", lambda: True
    )

    with pytest.raises(OSError, match=write_error_message) as failure:
        bump_toml_files.write_atomic_text(
            destination,
            "replacement",
            workspace_root=tmp_path,
        )

    assert any(cleanup_error_message in note for note in failure.value.__notes__), (
        "cleanup failure details should be attached to the original write error"
    )
    assert destination.read_text(encoding="utf-8") == "original", (
        "the failed bounded replacement must leave destination bytes unchanged"
    )
    temporary_paths = tuple(tmp_path.glob(".lading-*"))
    assert len(temporary_paths) == 1, "the injected cleanup failure should be visible"
    original_unlink(temporary_paths[0])
