"""Conditional atomic writes protect a planned source snapshot."""

from pathlib import Path

import pytest

from lading.commands import bump_toml_files
from lading.utils import file_lock

WORKSPACE_LOCK_ERROR = "workspace lock unavailable"


@pytest.mark.parametrize("use_bounded_writer", [False, True])
def test_conditional_write_rechecks_text_immediately_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    use_bounded_writer: bool,
) -> None:
    """A non-cooperating edit observed at final check remains untouched."""
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("original", encoding="utf-8")
    external_edit = "external edit"

    if use_bounded_writer:
        if not bump_toml_files._supports_bounded_directory_operations():
            pytest.skip("descriptor-relative replacement is unavailable")
        validate_bounded_text = bump_toml_files._validate_expected_bounded_text

        def edit_before_validation(
            directory_fd: int, filename: str, expected_text: str
        ) -> None:
            """Edit through the path after the writer pins its parent handle."""
            manifest.write_text(external_edit, encoding="utf-8")
            validate_bounded_text(directory_fd, filename, expected_text)

        validator_name = "_validate_expected_bounded_text"
    else:
        monkeypatch.setattr(
            bump_toml_files, "_supports_bounded_directory_operations", lambda: False
        )
        validate_text = bump_toml_files._validate_expected_current_text

        def edit_before_validation(path: Path, expected_text: str) -> None:
            """Model a tool that ignores the advisory Lading write lock."""
            path.write_text(external_edit, encoding="utf-8")
            validate_text(path, expected_text)

        validator_name = "_validate_expected_current_text"

    monkeypatch.setattr(bump_toml_files, validator_name, edit_before_validation)

    with pytest.raises(PermissionError, match="changed before replacement"):
        bump_toml_files.write_atomic_text(
            manifest,
            "replacement",
            workspace_root=workspace_root,
            expected_current_text="original",
        )

    assert manifest.read_text(encoding="utf-8") == external_edit, (
        "a changed source must not be replaced by a stale atomic write"
    )
    assert not list(manifest.parent.glob(".lading-*")), (
        "a rejected conditional write must remove its temporary file"
    )


def test_conditional_write_fails_without_an_exclusive_workspace_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer leaves its target unchanged when lock acquisition fails."""
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("original", encoding="utf-8")

    def fail_lock(_handle: object, *, locked: bool) -> None:
        """Report that the workspace lock cannot be established."""
        assert locked, "the write lock should be acquired before replacement"
        raise PermissionError(WORKSPACE_LOCK_ERROR)

    monkeypatch.setattr(file_lock, "set_exclusive_lock", fail_lock)

    with pytest.raises(PermissionError, match="workspace lock unavailable"):
        bump_toml_files.write_atomic_text(
            manifest,
            "replacement",
            workspace_root=workspace_root,
            expected_current_text="original",
        )

    assert manifest.read_text(encoding="utf-8") == "original", (
        "the target must remain untouched when the exclusive lock is unavailable"
    )
