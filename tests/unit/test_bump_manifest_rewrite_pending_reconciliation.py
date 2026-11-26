"""Reconcile pending non-member manifest replacements after a failure."""

import os
import typing as typ

import pytest

from lading.commands import (
    bump_pipeline_manifest_rewrites,
    bump_pipeline_pending_reconciliation,
    bump_toml_files,
)
from lading.commands.bump_toml_write_options import (
    AtomicWriteIdentity,
    AtomicWriteStatus,
)

pytestmark = pytest.mark.usefixtures("reset_process_metrics")

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("current_text", "expected_note"),
    [
        pytest.param("original text", "", id="unchanged-baseline"),
        pytest.param(
            "concurrent edit",
            "Manifest changed while a rewrite was pending",
            id="concurrent-edit",
        ),
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
        pending_write_statuses={manifest_path: AtomicWriteStatus()},
    )
    failure = OSError("injected write failure")
    changed_manifests: set[Path] = set()

    bump_pipeline_pending_reconciliation.reconcile_pending_manifest_rewrites(
        stage,
        changed_manifests,
        failure,
    )

    notes = getattr(failure, "__notes__", [])
    concurrent_edit_notes = [note for note in notes if str(manifest_path) in note]
    if expected_note:
        assert any(expected_note in note for note in concurrent_edit_notes), (
            "an unrecognized concurrent value should be reported"
        )
    else:
        assert not concurrent_edit_notes, (
            "an unchanged baseline should not be reported as a concurrent edit"
        )
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


def test_matching_text_without_a_completed_replacement_is_not_claimed(
    tmp_path: Path,
) -> None:
    """A competing process's matching text is not owned by this failed write."""
    manifest_path = tmp_path / "Cargo.toml"
    baseline_text = "original text"
    pending_text = "planned replacement"
    manifest_path.write_text(pending_text, encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        rollback_baselines={manifest_path: baseline_text},
        pending_manifest_contents={manifest_path: pending_text},
        pending_write_statuses={manifest_path: AtomicWriteStatus()},
    )
    failure = OSError("conditional write rejected before replacement")
    changed_manifests: set[Path] = set()

    bump_pipeline_pending_reconciliation.reconcile_pending_manifest_rewrites(
        stage,
        changed_manifests,
        failure,
    )

    assert manifest_path not in stage.written_manifest_contents, (
        "matching content without replacement evidence must not be attributed"
    )
    assert manifest_path in stage.pending_manifest_contents, (
        "an uncommitted write must remain pending and unclaimed"
    )
    assert manifest_path not in changed_manifests, (
        "another process's matching write must not enter rollback ownership"
    )


def test_pending_recovery_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """Recovery uses a non-blocking regular-file check for pending targets."""
    if (
        not hasattr(os, "mkfifo")
        or not bump_toml_files._supports_bounded_directory_operations()
    ):
        pytest.skip("non-blocking FIFO checks are unavailable")
    manifest_path = tmp_path / "Cargo.toml"
    os.mkfifo(manifest_path)
    pending_text = "planned replacement"
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=tmp_path,
        rollback_baselines={manifest_path: "original text"},
        pending_manifest_contents={manifest_path: pending_text},
        pending_write_statuses={manifest_path: AtomicWriteStatus()},
    )
    failure = OSError("injected writer failure")

    bump_pipeline_pending_reconciliation.reconcile_pending_manifest_rewrites(
        stage, set(), failure
    )

    assert manifest_path in stage.pending_manifest_contents, (
        "an unreadable FIFO must remain pending and unclaimed"
    )
    assert any("not a regular file" in note for note in failure.__notes__), (
        "the FIFO read failure must be recorded without blocking"
    )


@pytest.mark.parametrize(
    ("current_text", "expected_note", "identity_matches"),
    [
        pytest.param(
            "planned replacement",
            "identity changed while a rewrite was pending",
            False,
            id="matching-text",
        ),
        pytest.param(
            "concurrent edit",
            "Manifest changed while a rewrite was pending",
            False,
            id="unexpected-text",
        ),
        pytest.param(
            "concurrent edit",
            "Manifest changed while a rewrite was pending",
            True,
            id="unexpected-text-matching-identity",
        ),
    ],
)
def test_pending_recovery_reports_mismatched_replacement_evidence(
    tmp_path: Path,
    current_text: str,
    expected_note: str,
    *,
    identity_matches: bool,
) -> None:
    """Pending evidence that no longer matches the file is reported."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(current_text, encoding="utf-8")
    pending_text = "planned replacement"
    metadata = manifest_path.stat()
    identity = (
        AtomicWriteIdentity(device=metadata.st_dev, inode=metadata.st_ino)
        if identity_matches
        else AtomicWriteIdentity(device=1, inode=2)
    )
    status = AtomicWriteStatus(replacement_identity=identity)
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        rollback_baselines={manifest_path: "original text"},
        pending_manifest_contents={manifest_path: pending_text},
        pending_write_statuses={manifest_path: status},
    )
    failure = OSError("injected write failure")

    bump_pipeline_pending_reconciliation.reconcile_pending_manifest_rewrites(
        stage, set(), failure
    )

    assert any(expected_note in note for note in failure.__notes__), (
        "a pending write with mismatched evidence must be reported"
    )
    assert manifest_path.read_text(encoding="utf-8") == current_text, (
        "the current manifest contents must remain in place"
    )
