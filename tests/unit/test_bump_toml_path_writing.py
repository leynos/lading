"""Exercise temporary-file naming and cleanup in atomic path writes."""

import os
import stat
from pathlib import Path
from unittest import mock

import pytest

from lading.commands import bump_toml_files, bump_toml_write_options


class _ReadOnlyTemporaryFilesystem:
    """Simulate read-only temporary cleanup through patched filesystem calls."""

    def __init__(self, readonly_error_message: str) -> None:
        """Capture original operations and prepare simulated filesystem state."""
        self.original_unlink = Path.unlink
        self.original_chmod = os.chmod
        self.readonly_error_message = readonly_error_message
        self.chmodded_temporaries: set[Path] = set()
        self.unlink_attempts: list[tuple[Path, bool]] = []

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        """Reject unlink until chmod has made a temporary writable."""
        if path.name.startswith(".lading-"):
            is_writable = path in self.chmodded_temporaries
            self.unlink_attempts.append((path, is_writable))
            if not is_writable:
                raise PermissionError(self.readonly_error_message)
        self.original_unlink(path, missing_ok=missing_ok)

    def chmod(
        self,
        path: str | os.PathLike[str],
        mode: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        """Track temporary permissions and forward chmod to the real filesystem."""
        temporary_path = Path(path)
        if temporary_path.name.startswith(".lading-"):
            self._record_temporary_mode(temporary_path, mode)
        self.original_chmod(path, mode, follow_symlinks=follow_symlinks)

    def _record_temporary_mode(self, path: Path, mode: int) -> None:
        """Record whether the temporary currently has its owner-write bit."""
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
        bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
    )

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "bounded replacement must not exceed the filesystem's basename limit"
    )
    assert not list(tmp_path.glob(".lading-*")), (
        "bounded replacement must leave no temporary file"
    )


def test_bounded_writer_accepts_workspace_relative_destination(
    tmp_path: Path,
) -> None:
    """A relative destination resolves from the workspace root."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")

    bump_toml_files.write_atomic_text(
        Path("Cargo.toml"),
        "replacement",
        bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
    )

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "bounded writes must accept workspace-relative destinations"
    )


def test_path_fallback_cleans_temp_from_parent_moved_during_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup uses the pinned directory after its path is redirected."""
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
    failure_message = "simulated write failure"
    write_content = bump_toml_files._write_temporary_content

    def redirect_parent_then_fail(
        descriptor: int, content: str, existing_mode: int | None
    ) -> None:
        """Move the parent after temp creation and fail the simulated write."""
        write_content(descriptor, content, existing_mode)
        parent.rename(moved_parent)
        parent.symlink_to(outside, target_is_directory=True)
        raise OSError(failure_message)

    monkeypatch.setattr(
        bump_toml_files, "_write_temporary_content", redirect_parent_then_fail
    )

    with pytest.raises(OSError, match=failure_message):
        bump_toml_files._write_path_based_atomic_text(manifest, "replacement")
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
    """Path cleanup failures are notes on the original write error."""
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_stable_temporary_cleanup",
        lambda: False,
    )
    write_error_message = "write denied"

    write_content = bump_toml_files._write_temporary_content

    def reject_content(
        descriptor: int, content: str, existing_mode: int | None
    ) -> None:
        """Fail after writing the temporary so cleanup is exercised."""
        write_content(descriptor, content, existing_mode)
        raise PermissionError(write_error_message)

    monkeypatch.setattr(bump_toml_files, "_write_temporary_content", reject_content)
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

    write_content = bump_toml_files._write_temporary_content

    def reject_content(
        descriptor: int, content: str, existing_mode: int | None
    ) -> None:
        """Fail after writing the temporary so cleanup is exercised."""
        write_content(descriptor, content, existing_mode)
        raise PermissionError(replacement_error_message)

    monkeypatch.setattr(bump_toml_files, "_write_temporary_content", reject_content)
    readonly_error_message = "read-only temporary"
    readonly_filesystem = _ReadOnlyTemporaryFilesystem(readonly_error_message)

    with (
        mock.patch.object(
            Path, "unlink", autospec=True, side_effect=readonly_filesystem.unlink
        ),
        mock.patch.object(os, "chmod", side_effect=readonly_filesystem.chmod),
        pytest.raises(PermissionError, match=replacement_error_message),
    ):
        bump_toml_files._write_path_based_atomic_text(
            destination,
            "replacement",
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


def test_path_cleanup_does_not_change_a_symlink_target_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed unlink cannot make a substituted link target writable."""
    target = tmp_path / "outside.toml"
    target.write_text("outside", encoding="utf-8")
    target.chmod(stat.S_IREAD)
    original_mode = stat.S_IMODE(target.stat().st_mode)
    temporary_path = tmp_path / ".lading-temporary"
    try:
        temporary_path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")
    original_unlink = Path.unlink

    def reject_temporary_unlink(path: Path, *, missing_ok: bool = False) -> None:
        """Force cleanup to enter its writable retry for the substituted link."""
        if path == temporary_path:
            readonly_error_message = "simulated read-only temporary"
            raise PermissionError(readonly_error_message)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", reject_temporary_unlink)
    primary_error = OSError("replacement failed")

    bump_toml_files.bump_toml_temporary_cleanup.cleanup_path_based_temporary_file(
        temporary_path.name,
        temporary_path,
        None,
        primary_error,
    )

    assert temporary_path.is_symlink(), "failed cleanup must leave the link in place"
    assert stat.S_IMODE(target.stat().st_mode) == original_mode, (
        "cleanup must not change the linked target's permission bits"
    )
    assert any("writable retry failed" in note for note in primary_error.__notes__), (
        "the safe no-follow retry failure should be recorded on the write error"
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
            bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
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
