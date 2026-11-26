"""Conditional atomic writes protect a planned source snapshot."""

import collections.abc as cabc
import contextlib
import os
from pathlib import Path

import pytest

from lading.commands import (
    bump_toml_bounded_filesystem,
    bump_toml_files,
    bump_toml_write_options,
    bump_workspace_write_lock,
)
from lading.commands.bump_toml_write_options import (
    AtomicWriteOptions,
    AtomicWriteStatus,
)
from lading.utils import file_lock

WORKSPACE_LOCK_ERROR = "workspace lock unavailable"


@pytest.fixture(autouse=True)
def isolate_workspace_write_lock_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep atomic-write lock files inside this test's temporary directory."""
    lock_directory = tmp_path / "workspace-write-locks"
    monkeypatch.setattr(
        bump_workspace_write_lock,
        "_workspace_lock_directory",
        lambda: lock_directory,
    )


def test_conditional_write_rechecks_text_immediately_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-cooperating edit observed at final check remains untouched."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable")
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("original", encoding="utf-8")
    external_edit = "external edit"
    write_status = AtomicWriteStatus()

    validate_bounded_text = bump_toml_bounded_filesystem._validate_expected_bounded_text

    def edit_before_validation(
        directory_fd: int,
        filename: str,
        expected_text: str | None,
        expected_identity: bump_toml_write_options.AtomicWriteIdentity | None,
    ) -> None:
        """Edit through the path after the writer pins its parent handle."""
        manifest.write_text(external_edit, encoding="utf-8")
        validate_bounded_text(directory_fd, filename, expected_text, expected_identity)

    monkeypatch.setattr(
        bump_toml_bounded_filesystem,
        "_validate_expected_bounded_text",
        edit_before_validation,
    )

    with pytest.raises(PermissionError, match="changed before replacement"):
        bump_toml_files.write_atomic_text(
            manifest,
            "replacement",
            AtomicWriteOptions(
                workspace_root=workspace_root,
                expected_current_text="original",
                status=write_status,
            ),
        )

    assert manifest.read_text(encoding="utf-8") == external_edit, (
        "a changed source must not be replaced by a stale atomic write"
    )
    assert not write_status.replacement_completed, (
        "a rejected stale write must retain its uncommitted status"
    )
    assert not list(manifest.parent.glob(".lading-*")), (
        "a rejected conditional write must remove its temporary file"
    )


def test_conditional_write_rechecks_identity_when_text_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identical replacement by another writer fails the identity check."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable")
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("planned text", encoding="utf-8")
    prior_status = AtomicWriteStatus()
    bump_toml_files.write_atomic_text(
        manifest,
        "planned text",
        AtomicWriteOptions(workspace_root=workspace_root, status=prior_status),
    )
    expected_identity = prior_status.replacement_identity
    assert expected_identity is not None, (
        "the prior replacement should provide an identity precondition"
    )
    validate_bounded_text = bump_toml_bounded_filesystem._validate_expected_bounded_text

    def replace_with_identical_text(
        directory_fd: int,
        filename: str,
        expected_text: str | None,
        expected_identity: bump_toml_write_options.AtomicWriteIdentity | None,
    ) -> None:
        """Replace the path with a new inode while keeping its text identical."""
        competing_replacement = manifest.with_name("competing-Cargo.toml")
        competing_replacement.write_text("planned text", encoding="utf-8")
        competing_replacement.replace(manifest)
        validate_bounded_text(directory_fd, filename, expected_text, expected_identity)

    monkeypatch.setattr(
        bump_toml_bounded_filesystem,
        "_validate_expected_bounded_text",
        replace_with_identical_text,
    )
    status = AtomicWriteStatus()

    with pytest.raises(PermissionError, match="identity changed"):
        bump_toml_files.write_atomic_text(
            manifest,
            "new text",
            AtomicWriteOptions(
                workspace_root=workspace_root,
                expected_current_text="planned text",
                expected_current_identity=expected_identity,
                status=status,
            ),
        )

    assert manifest.read_text(encoding="utf-8") == "planned text", (
        "the identity mismatch must leave the matching replacement untouched"
    )
    assert not status.replacement_completed, (
        "a refused identity check must not claim that replacement completed"
    )


@pytest.mark.timeout(10)
def test_conditional_write_rejects_fifo_replacement_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FIFO swapped into place at final validation is rejected promptly."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable")
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes are unavailable")
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("original", encoding="utf-8")
    validate_bounded_text = bump_toml_bounded_filesystem._validate_expected_bounded_text

    def replace_with_fifo(
        directory_fd: int,
        filename: str,
        expected_text: str | None,
        expected_identity: bump_toml_write_options.AtomicWriteIdentity | None,
    ) -> None:
        """Replace the planned regular file before its descriptor is opened."""
        manifest.unlink()
        os.mkfifo(manifest)
        validate_bounded_text(directory_fd, filename, expected_text, expected_identity)

    monkeypatch.setattr(
        bump_toml_bounded_filesystem,
        "_validate_expected_bounded_text",
        replace_with_fifo,
    )
    with pytest.raises(PermissionError, match="not a regular file"):
        bump_toml_files.write_atomic_text(
            manifest,
            "replacement",
            AtomicWriteOptions(
                workspace_root=workspace_root,
                expected_current_text="original",
            ),
        )

    assert not list(manifest.parent.glob(".lading-*")), (
        "a rejected FIFO destination must remove the temporary file"
    )


def test_workspace_write_lock_creates_its_lock_directory(tmp_path: Path) -> None:
    """A resolved workspace lock is created in the configured test directory."""
    lock_directory = tmp_path / "workspace-write-locks"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with bump_workspace_write_lock.workspace_write_lock(workspace_root.resolve()):
        assert lock_directory.is_dir(), "the lock directory must exist while locked"


def test_workspace_write_lock_is_reentrant_for_nested_atomic_writes(
    tmp_path: Path,
) -> None:
    """A pipeline lock can enclose the writer's own lock boundary."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with (
        bump_workspace_write_lock.workspace_write_lock(
            workspace_root.resolve()
        ) as outer,
        bump_workspace_write_lock.workspace_write_lock(
            workspace_root.resolve()
        ) as inner,
    ):
        assert inner == outer, "nested writes must retain the acquired identity"


def test_bounded_writer_rejects_replaced_workspace_root(
    tmp_path: Path,
) -> None:
    """The opened root must still be the directory whose lock is held."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest = workspace_root / "Cargo.toml"
    manifest.write_text("original", encoding="utf-8")
    displaced_root = tmp_path / "workspace-displaced"

    with bump_workspace_write_lock.workspace_write_lock(workspace_root.resolve()):
        workspace_root.rename(displaced_root)
        workspace_root.mkdir()
        replacement_manifest = workspace_root / "Cargo.toml"
        replacement_manifest.write_text("replacement root", encoding="utf-8")

        with pytest.raises(PermissionError, match="root changed after acquiring"):
            bump_toml_files.write_atomic_text(
                Path("Cargo.toml"),
                "must not be written",
                AtomicWriteOptions(workspace_root=workspace_root),
            )

    assert replacement_manifest.read_text(encoding="utf-8") == "replacement root", (
        "a replacement directory must not be written under the displaced root lock"
    )


def test_bounded_writer_uses_the_root_resolved_before_lock_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retargeting the root alias cannot move a write to another workspace."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable")
    original_workspace = tmp_path / "workspace-original"
    redirected_workspace = tmp_path / "workspace-redirected"
    original_workspace.mkdir()
    redirected_workspace.mkdir()
    original_manifest = original_workspace / "Cargo.toml"
    redirected_manifest = redirected_workspace / "Cargo.toml"
    original_manifest.write_text("original", encoding="utf-8")
    redirected_manifest.write_text("redirected", encoding="utf-8")
    workspace_alias = tmp_path / "workspace"
    workspace_alias.symlink_to(original_workspace, target_is_directory=True)
    locked_roots: list[Path] = []

    @contextlib.contextmanager
    def retarget_alias_after_lock(
        resolved_root: Path,
    ) -> cabc.Generator[bump_toml_write_options.AtomicWriteIdentity]:
        """Move the alias only after the writer selected its lock identity."""
        locked_roots.append(resolved_root)
        identity = bump_workspace_write_lock._workspace_identity(resolved_root)
        workspace_alias.unlink()
        workspace_alias.symlink_to(redirected_workspace, target_is_directory=True)
        yield identity

    monkeypatch.setattr(
        bump_workspace_write_lock,
        "workspace_write_lock",
        retarget_alias_after_lock,
    )

    bump_toml_files.write_atomic_text(
        Path("Cargo.toml"),
        "replacement",
        AtomicWriteOptions(workspace_root=workspace_alias),
    )

    assert locked_roots == [original_workspace.resolve()], (
        "the lock and bounded writer must use one canonical root"
    )
    assert original_manifest.read_text(encoding="utf-8") == "replacement", (
        "the write must remain anchored to the root selected before locking"
    )
    assert redirected_manifest.read_text(encoding="utf-8") == "redirected", (
        "a retargeted workspace alias must not receive the write"
    )


def test_conditional_write_fails_without_an_exclusive_workspace_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer leaves its target unchanged when lock acquisition fails."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable")
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
            AtomicWriteOptions(
                workspace_root=workspace_root,
                expected_current_text="original",
            ),
        )

    assert manifest.read_text(encoding="utf-8") == "original", (
        "the target must remain untouched when the exclusive lock is unavailable"
    )
