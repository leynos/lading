"""Ensure Ctrl-C restores completed manifest writes in a version bump."""

from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_manifest_rewrites,
    bump_manifests,
    bump_pipeline,
    bump_pipeline_crate_manifests,
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.utils import file_lock
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case

WORKSPACE_LOCK_ERROR = "workspace lock unavailable"


def test_member_interruption_restores_completed_manifest_writes(
    tmp_path: Path,
) -> None:
    """An interrupt during member updates restores earlier successful writes."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    interrupted_manifest = case.workspace.crates[0].manifest_path
    original_update_manifest = bump_pipeline._update_manifest
    interrupted_after_write: list[Path] = []

    def interrupt_selected_manifest(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        update_context: bump_manifests._ManifestUpdateContext,
    ) -> bump_manifests._ManifestUpdateResult:
        """Interrupt after the first crate write but before its caller resumes."""
        result = original_update_manifest(
            manifest_path,
            selectors,
            target_version,
            update_context,
        )
        if manifest_path == interrupted_manifest and result.was_changed:
            interrupted_after_write.append(manifest_path)
            raise KeyboardInterrupt
        return result

    with (
        mock.patch.object(
            bump_pipeline_crate_manifests,
            "_update_manifest",
            side_effect=interrupt_selected_manifest,
        ),
        pytest.raises(KeyboardInterrupt),
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

    assert interrupted_after_write == [interrupted_manifest], (
        "the interruption must follow an atomic write and precede caller bookkeeping"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "member interruption must restore every completed workspace manifest write"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "member interruption must leave not-yet-applied fixture rewrites untouched"
    )


def test_member_update_rejects_a_changed_symlink_destination(
    tmp_path: Path,
) -> None:
    """Repeated updates cannot follow a symlink redirected after an earlier write."""
    original_destination = tmp_path / "original.toml"
    redirected_destination = tmp_path / "redirected.toml"
    manifest_path = tmp_path / "Cargo.toml"
    previous_output = '[package]\nname = "alpha"\nversion = "1.2.3"\n'
    original_destination.write_text(previous_output, encoding="utf-8")
    redirected_destination.write_text(previous_output, encoding="utf-8")
    manifest_path.symlink_to(original_destination)
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest_path: previous_output},
        written_manifest_destinations={manifest_path: original_destination.resolve()},
    )
    manifest_path.unlink()
    manifest_path.symlink_to(redirected_destination)

    with pytest.raises(ManifestRewriteError, match="destination changed"):
        bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
            manifest_path, stage
        )


def test_non_member_interruption_restores_completed_rewrites_and_members(
    tmp_path: Path,
) -> None:
    """An interrupt during fixture application rolls back completed writes."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text
    completed_fixture_writes: list[Path] = []

    def interrupt_second_fixture(
        path: Path,
        content: str,
        *,
        workspace_root: Path | None = None,
        expected_current_text: str | None = None,
    ) -> None:
        """Write the first fixture, then interrupt before the second one."""
        if path == case.fixtures[1].resolve():
            raise KeyboardInterrupt
        write_atomic_text(
            path,
            content,
            workspace_root=workspace_root,
            expected_current_text=expected_current_text,
        )
        completed_fixture_writes.append(path)

    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=interrupt_second_fixture,
        ),
        pytest.raises(KeyboardInterrupt),
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

    assert completed_fixture_writes == [case.fixtures[0].resolve()], (
        "the first fixture must be written before the injected interruption"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "interruption recovery must restore every completed fixture rewrite"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "interruption recovery must restore member manifests written earlier"
    )


def test_interruption_does_not_claim_an_unattempted_matching_plan(
    tmp_path: Path,
) -> None:
    """Only a completed or in-progress write can be attributed to this bump."""
    completed_path = tmp_path / "fixtures/completed/Cargo.toml"
    unattempted_path = tmp_path / "fixtures/unattempted/Cargo.toml"
    completed_path.parent.mkdir(parents=True)
    unattempted_path.parent.mkdir(parents=True)
    original = "original"
    completed_output = "completed output"
    external_output = "same as planned output"
    completed_path.write_text(completed_output, encoding="utf-8")
    unattempted_path.write_text(external_output, encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        plans=(
            bump_manifest_rewrites.ManifestRewritePlan(
                completed_path, original, completed_output
            ),
            bump_manifest_rewrites.ManifestRewritePlan(
                unattempted_path, original, external_output
            ),
        ),
        written_manifest_contents={completed_path: completed_output},
        written_manifest_destinations={completed_path: completed_path},
        rollback_baselines={
            completed_path: original,
            unattempted_path: original,
        },
    )
    changed_manifests: set[Path] = set()

    bump_pipeline_manifest_recovery.record_completed_rewrites_for_interruption(
        stage, changed_manifests, KeyboardInterrupt()
    )
    rollback_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        changed_manifests,
        RuntimeError("interrupted"),
        workspace_root=tmp_path,
    )

    assert rollback_error is None, "the known completed write should be restored"
    assert completed_path.read_text(encoding="utf-8") == original, (
        "the completed fixture write must be restored"
    )
    assert unattempted_path.read_text(encoding="utf-8") == external_output, (
        "an unattempted path matching its planned output must remain untouched"
    )


def test_rollback_rejects_a_superseded_completed_output(
    tmp_path: Path,
) -> None:
    """Only the latest completed output is eligible for automatic rollback."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("older output", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest: "latest output"},
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


def test_rollback_leaves_output_when_workspace_lock_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery reports a lock failure without replacing the current output."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("latest output", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        written_manifest_contents={manifest: "latest output"},
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
    assert "workspace lock unavailable" in str(rollback_error), (
        "the recovery error should retain the lock acquisition failure"
    )
    assert manifest.read_text(encoding="utf-8") == "latest output", (
        "recovery must leave the file unchanged when it cannot hold the lock"
    )
