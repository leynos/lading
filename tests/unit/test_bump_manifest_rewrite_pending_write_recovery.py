"""Recover manifest batches when a writer fails after replacing a file."""

import os
from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_manifest_rewrites,
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_restore,
    bump_pipeline_manifest_rewrites,
    bump_toml_files,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteWriteError
from lading.commands.bump_toml_write_options import (
    AtomicWriteOptions,
    AtomicWriteStatus,
)
from tests.helpers.manifest_rewrite_recovery import (
    _make_partial_write_failure_case,
    _pending_recovery_stage,
)

COMPETING_WRITE_REJECTION = "manifest changed before replacement"
RENAME_INTERRUPT_MESSAGE = "interrupted immediately after rename"
RECOVERY_INTERRUPT_MESSAGE = "recover interrupted replacement"

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


def _interrupt_after_rename(
    monkeypatch: pytest.MonkeyPatch,
    manifest_path: Path,
    pending_text: str,
    status: AtomicWriteStatus,
) -> None:
    """Write a manifest and interrupt immediately after the first rename."""
    original_rename = os.rename
    interrupt_once = True

    def rename_then_interrupt(
        source: str | bytes,
        destination: str | bytes,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        """Interrupt after the real rename, before the writer status advances."""
        nonlocal interrupt_once
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if interrupt_once:
            interrupt_once = False
            raise KeyboardInterrupt(RENAME_INTERRUPT_MESSAGE)

    monkeypatch.setattr(bump_toml_files.os, "rename", rename_then_interrupt)
    monkeypatch.setattr(
        bump_toml_files.os,
        "supports_dir_fd",
        os.supports_dir_fd | {rename_then_interrupt},
    )
    with pytest.raises(KeyboardInterrupt, match=RENAME_INTERRUPT_MESSAGE):
        bump_toml_files.write_atomic_text(
            manifest_path,
            pending_text,
            AtomicWriteOptions(
                workspace_root=manifest_path.parent,
                status=status,
            ),
        )


def test_recovery_owns_rename_completed_before_status_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The temporary identity covers interruption immediately after rename."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest_path = tmp_path / "Cargo.toml"
    original_text = 'version = "1.0.0"\n'
    pending_text = 'version = "2.0.0"\n'
    manifest_path.write_text(original_text, encoding="utf-8", newline="")
    status = AtomicWriteStatus()

    _interrupt_after_rename(monkeypatch, manifest_path, pending_text, status)

    assert status.replacement_identity is not None, (
        "the temporary identity must be published before rename"
    )
    assert not status.replacement_completed, (
        "the injected interrupt must land before the completion flag"
    )
    stage = _pending_recovery_stage(manifest_path, original_text, pending_text, status)
    failure = KeyboardInterrupt(RECOVERY_INTERRUPT_MESSAGE)
    changed_manifests: set[Path] = set()

    bump_pipeline_manifest_recovery.reconcile_pending_manifest_rewrites(
        stage, changed_manifests, failure
    )
    rollback_error = bump_pipeline_manifest_restore.restore_changed_manifests(
        stage,
        changed_manifests,
        failure,
        workspace_root=tmp_path,
    )

    assert rollback_error is None, "the owned replacement must restore cleanly"
    assert manifest_path.read_text(encoding="utf-8") == original_text, (
        "recovery must restore the replacement even before the status flag"
    )


def test_rollback_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """Rollback checks file type before reading a replaced destination."""
    if (
        not hasattr(os, "mkfifo")
        or not bump_toml_files._supports_bounded_directory_operations()
    ):
        pytest.skip("non-blocking FIFO checks are unavailable")
    manifest_path = tmp_path / "Cargo.toml"
    os.mkfifo(manifest_path)
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest_path, "original text", "planned replacement"
    )
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=tmp_path,
        plans=(plan,),
        rollback_baselines={manifest_path: "original text"},
        written_manifest_contents={manifest_path: "planned replacement"},
        written_manifest_destinations={manifest_path: manifest_path},
    )

    rollback_error = bump_pipeline_manifest_restore.restore_changed_manifests(
        stage,
        {manifest_path},
        OSError("injected later-stage failure"),
        workspace_root=tmp_path,
    )

    assert isinstance(rollback_error, ManifestRewriteWriteError), (
        "the non-regular rollback target must be reported as a write failure"
    )
    assert manifest_path.is_fifo(), "rollback must leave the FIFO in place"


def test_failed_conditional_write_preserves_competing_matching_manifest(
    tmp_path: Path,
) -> None:
    """A stale writer cannot roll back another process's identical output."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    case = _make_partial_write_failure_case(tmp_path)
    first_fixture = case.fixtures[0].resolve()
    original_write = bump_manifest_rewrite_application.write_atomic_text
    competing_contents: list[bytes] = []

    def competing_write_then_reject(
        path: Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Simulate another process committing the plan before stale rejection."""
        if path == first_fixture:
            competing_contents.append(content.encode("utf-8"))
            path.write_text(content, encoding="utf-8", newline="")
            raise PermissionError(COMPETING_WRITE_REJECTION)
        original_write(
            path,
            content,
            write_options,
        )

    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=competing_write_then_reject,
        ),
        pytest.raises(ManifestRewriteWriteError) as failure,
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert failure.value.failed_path == first_fixture, (
        "the failed stale write should identify its manifest"
    )
    assert failure.value.completed_writes == 0, (
        "another process's matching output is not a completed write by this bump"
    )
    assert competing_contents == [first_fixture.read_bytes()], (
        "the exact competing write must remain in place after recovery"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "the bump's member writes should roll back without claiming the fixture"
    )


def test_post_replace_oserror_restores_pending_manifest_write(
    tmp_path: Path,
) -> None:
    """A writer error after replacement still rolls back the pending file."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    original_write = bump_manifest_rewrite_application.write_atomic_text
    first_fixture = case.fixtures[0].resolve()
    failure_message = "failure after atomic replacement"

    def replace_then_raise(
        path: Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Raise an ordinary error after the selected atomic replace commits."""
        original_write(
            path,
            content,
            write_options,
        )
        if path == first_fixture:
            raise OSError(failure_message)

    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=replace_then_raise,
        ),
        pytest.raises(ManifestRewriteWriteError, match=failure_message),
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "a pending fixture replacement must be included in rollback"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "workspace manifests written earlier must also be restored"
    )
