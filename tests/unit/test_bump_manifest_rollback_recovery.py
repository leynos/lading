"""Verify rollback ownership for interrupted manifest rewrites."""

from pathlib import Path

import pytest

from lading.commands import (
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_restore,
    bump_pipeline_manifest_rewrites,
    bump_toml_files,
    bump_toml_write_options,
)
from lading.commands.bump_toml_bounded_filesystem import _read_bounded_regular_text
from lading.utils import file_lock

WORKSPACE_LOCK_ERROR = "workspace lock unavailable"
DESCRIPTOR_CLOSE_FAILURE = "descriptor close failed"


def test_rollback_rejects_a_superseded_completed_output(tmp_path: Path) -> None:
    """Only the latest completed output is eligible for automatic rollback."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("older output", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest: "latest output"},
        written_manifest_identities={
            manifest: _read_bounded_regular_text(manifest, tmp_path)[1]
        },
        written_manifest_destinations={manifest: manifest},
        rollback_baselines={manifest: "original"},
    )

    rollback_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        set(),
        RuntimeError("later stage failed"),
        workspace_root=tmp_path,
    )

    assert rollback_error is not None, "a superseded output must be reported"
    assert manifest.read_text(encoding="utf-8") == "older output", (
        "rollback must preserve text that is not the latest completed output"
    )


def test_rollback_preserves_identical_output_from_a_later_write(
    tmp_path: Path,
) -> None:
    """A later atomic write with identical text is not owned by this bump."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest = tmp_path / "Cargo.toml"
    baseline = "original"
    output = "latest output"
    manifest.write_text(baseline, encoding="utf-8")
    first_status = bump_toml_write_options.AtomicWriteStatus()
    bump_toml_files.write_atomic_text(
        manifest,
        output,
        bump_toml_write_options.AtomicWriteOptions(
            workspace_root=tmp_path,
            status=first_status,
        ),
    )
    first_identity = first_status.replacement_identity
    assert first_identity is not None, (
        "the first replacement must capture its filesystem identity"
    )
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest: output},
        written_manifest_identities={manifest: first_identity},
        written_manifest_destinations={manifest: manifest},
        rollback_baselines={manifest: baseline},
    )
    second_status = bump_toml_write_options.AtomicWriteStatus()
    bump_toml_files.write_atomic_text(
        manifest,
        output,
        bump_toml_write_options.AtomicWriteOptions(
            workspace_root=tmp_path,
            status=second_status,
        ),
    )

    rollback_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        {manifest},
        RuntimeError("later stage failed"),
        workspace_root=tmp_path,
    )

    assert rollback_error is not None, "the superseded write should be reported"
    assert "replacement identity changed" in str(rollback_error), (
        "rollback should report that another replacement owns the current file"
    )
    assert second_status.replacement_identity != first_status.replacement_identity, (
        "each atomic replacement must have a distinct filesystem identity"
    )
    assert manifest.read_text(encoding="utf-8") == output, (
        "rollback must leave the later writer's identical output untouched"
    )


def test_rollback_reports_replacement_before_later_writer_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery reports a completed restore if descriptor cleanup then fails."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest = tmp_path / "Cargo.toml"
    baseline = "original"
    output = "latest output"
    manifest.write_text(baseline, encoding="utf-8")
    status = bump_toml_write_options.AtomicWriteStatus()
    bump_toml_files.write_atomic_text(
        manifest,
        output,
        bump_toml_write_options.AtomicWriteOptions(
            workspace_root=tmp_path,
            status=status,
        ),
    )
    assert status.replacement_identity is not None, (
        "the completed output must carry an identity for rollback"
    )
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest: output},
        written_manifest_identities={manifest: status.replacement_identity},
        written_manifest_destinations={manifest: manifest},
        rollback_baselines={manifest: baseline},
    )
    original_write = bump_pipeline_manifest_restore.write_atomic_text

    def replace_then_raise(
        path: Path,
        content: str,
        write_options: bump_toml_write_options.AtomicWriteOptions | None = None,
    ) -> None:
        """Raise after the restoration replacement is already visible."""
        original_write(path, content, write_options)
        assert write_options is not None, "rollback should provide write options"
        assert write_options.status is not None, (
            "rollback should provide write evidence"
        )
        assert write_options.status.replacement_completed, (
            "the writer should mark the restore before the later error"
        )
        raise OSError(DESCRIPTOR_CLOSE_FAILURE)

    monkeypatch.setattr(
        bump_pipeline_manifest_restore, "write_atomic_text", replace_then_raise
    )
    rollback_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        {manifest},
        RuntimeError("later stage failed"),
        workspace_root=tmp_path,
    )

    assert rollback_error is not None, "the post-replacement error must be reported"
    assert "rollback replacement completed" in str(rollback_error), (
        "recovery must distinguish a completed restore from a failed restore"
    )
    assert manifest.read_text(encoding="utf-8") == baseline, (
        "the original manifest should be restored despite the cleanup error"
    )


def test_rollback_leaves_output_when_workspace_lock_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery reports a lock failure without replacing the current output."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("latest output", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest: "latest output"},
        written_manifest_identities={
            manifest: _read_bounded_regular_text(manifest, tmp_path)[1]
        },
        written_manifest_destinations={manifest: manifest},
        rollback_baselines={manifest: "original"},
    )

    def fail_lock(_handle: object, *, locked: bool) -> None:
        """Prevent recovery from claiming the workspace write lock."""
        assert locked, "the write lock should be acquired before recovery"
        raise PermissionError(WORKSPACE_LOCK_ERROR)

    monkeypatch.setattr(file_lock, "set_exclusive_lock", fail_lock)
    rollback_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        set(),
        RuntimeError("later stage failed"),
        workspace_root=tmp_path,
    )

    assert rollback_error is not None, "the refused recovery must be reported"
    assert WORKSPACE_LOCK_ERROR in str(rollback_error), (
        "the recovery error must retain the lock acquisition failure"
    )
    assert manifest.read_text(encoding="utf-8") == "latest output", (
        "recovery must leave the manifest unchanged without the lock"
    )
