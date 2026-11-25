"""Exercise temporary-file naming and cleanup in atomic path writes."""

from pathlib import Path

import pytest

from lading.commands import bump_toml_files


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
