"""Recover manifest batches when a writer fails after replacing a file."""

from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteWriteError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


@pytest.mark.parametrize(
    ("current_text", "expected_note"),
    [
        ("original text", ""),
        ("concurrent edit", "Manifest changed while a rewrite was pending"),
    ],
)
def test_pending_rewrite_recovery_reports_only_unrecognized_contents(
    tmp_path: Path,
    current_text: str,
    expected_note: str,
) -> None:
    """Report concurrent pending-file edits while preserving baseline no-ops."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(current_text, encoding="utf-8")
    baseline_text = "original text"
    pending_text = "planned replacement"
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        rollback_baselines={manifest_path: baseline_text},
        pending_manifest_contents={manifest_path: pending_text},
    )
    failure = OSError("injected write failure")
    changed_manifests: set[Path] = set()

    bump_pipeline_manifest_recovery.reconcile_pending_manifest_rewrites(
        stage,
        changed_manifests,
        failure,
    )

    notes = getattr(failure, "__notes__", [])
    concurrent_edit_notes = [note for note in notes if str(manifest_path) in note]
    assert any(expected_note in note for note in concurrent_edit_notes) is bool(
        expected_note
    ), "only an unrecognized current value should be reported as another edit"
    assert manifest_path.read_text(encoding="utf-8") == current_text, (
        "reconciliation must leave pending contents untouched"
    )
    assert manifest_path in stage.pending_manifest_contents, (
        "unconfirmed writes must remain pending for the next recovery decision"
    )
    assert manifest_path not in stage.written_manifest_contents, (
        "mismatched contents must not be attributed to a completed write"
    )
    assert manifest_path not in changed_manifests, (
        "an unconfirmed write must not be reported as completed"
    )


def test_post_replace_oserror_restores_pending_manifest_write(
    tmp_path: Path,
) -> None:
    """A writer error after replacement still rolls back the pending file."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    original_write = bump_manifest_rewrite_application.write_atomic_text
    first_fixture = case.fixtures[0].resolve()
    failure_message = "failure after atomic replacement"

    def replace_then_raise(
        path: Path,
        content: str,
        *,
        workspace_root: Path | None = None,
        expected_current_text: str | None = None,
    ) -> None:
        """Raise an ordinary error after the selected atomic replace commits."""
        original_write(
            path,
            content,
            workspace_root=workspace_root,
            expected_current_text=expected_current_text,
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
