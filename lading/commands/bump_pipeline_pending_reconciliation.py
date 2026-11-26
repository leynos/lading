"""Reconcile pending non-member manifest replacements after a failure.

The recovery orchestration registers writes and decides when to restore them.
This module owns the narrower question of whether a prepared replacement that
did not reach its completion callback is now present on disk.
"""

import dataclasses as dc
import typing as typ

from lading.commands.bump_toml_bounded_filesystem import _read_bounded_regular_text

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )
    from lading.commands.bump_toml_write_options import (
        AtomicWriteIdentity,
        AtomicWriteStatus,
    )


def reconcile_pending_manifest_rewrites(
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
    failure: BaseException,
) -> None:
    """Record only pending replacements that reached their planned content.

    Parameters
    ----------
    stage
        Stage whose pending and completed output maps are reconciled.
    changed_manifests
        Mutable path set to which verified pending writes are added.
    failure
        Active error that receives a note if a pending path cannot be inspected.

    Notes
    -----
    Pending writes are confirmed only when identity and contents match. Others
    stay pending; confirmed paths update ``changed_manifests``.
    """
    context = _Reconciliation(stage, changed_manifests, failure)
    for manifest_path, pending_text in tuple(stage.pending_manifest_contents.items()):
        _reconcile_pending_manifest_rewrite(
            context,
            _PendingRewrite(
                manifest_path,
                pending_text,
                stage.pending_write_statuses.get(manifest_path),
            ),
        )


@dc.dataclass(frozen=True, slots=True)
class _PendingRewrite:
    """One replacement that was prepared but not confirmed by its callback."""

    path: Path
    pending_text: str
    status: AtomicWriteStatus | None


@dc.dataclass(slots=True)
class _Reconciliation:
    """Recovery state shared while reconciling pending replacements."""

    stage: _ManifestRewriteStage
    changed_manifests: set[Path]
    failure: BaseException


def _reconcile_pending_manifest_rewrite(
    context: _Reconciliation,
    pending: _PendingRewrite,
) -> None:
    """Reconcile one pending write against its current file contents."""
    current_output = _read_pending_output(context, pending)
    if current_output is None:
        return
    if not _pending_write_matches(pending, current_output):
        _report_pending_mismatch(context, pending, current_output)
        return
    stage = context.stage
    stage.written_manifest_contents[pending.path] = pending.pending_text
    stage.written_manifest_identities[pending.path] = current_output[1]
    stage.pending_manifest_contents.pop(pending.path)
    stage.pending_write_statuses.pop(pending.path, None)
    context.changed_manifests.add(pending.path)


def _read_pending_output(
    context: _Reconciliation,
    pending: _PendingRewrite,
) -> tuple[str, AtomicWriteIdentity] | None:
    """Read one pending manifest, noting an inspection failure on the error."""
    workspace_root = context.stage.workspace_root or pending.path.parent
    try:
        return _read_bounded_regular_text(pending.path, workspace_root)
    except (OSError, UnicodeError) as exc:
        context.failure.add_note(
            f"Could not inspect manifest rewrite target {pending.path} "
            f"during recovery: {exc}"
        )
        return None


def _pending_write_matches(
    pending: _PendingRewrite,
    current_output: tuple[str, AtomicWriteIdentity],
) -> bool:
    """Return whether a pending write's identity and text match the file."""
    current_text, current_identity = current_output
    return (
        pending.status is not None
        and pending.status.replacement_identity is not None
        and pending.status.replacement_identity == current_identity
        and current_text == pending.pending_text
    )


def _report_pending_mismatch(
    context: _Reconciliation,
    pending: _PendingRewrite,
    current_output: tuple[str, AtomicWriteIdentity],
) -> None:
    """Attach the diagnostic that fits one pending-write mismatch."""
    current_identity = current_output[1]
    recorded_identity = (
        None if pending.status is None else pending.status.replacement_identity
    )
    if recorded_identity is None:
        _report_unless_known_text(context, pending, current_output)
        return
    if recorded_identity != current_identity:
        _report_identity_change(context, pending, current_output)
        return
    _report_unrecognized_pending_contents(context, pending, current_output)


def _report_unless_known_text(
    context: _Reconciliation,
    pending: _PendingRewrite,
    current_output: tuple[str, AtomicWriteIdentity],
) -> None:
    """Report contents unknown to the baseline or the pending replacement."""
    known_texts = {
        context.stage.rollback_baselines.get(pending.path),
        pending.pending_text,
    }
    if current_output[0] not in known_texts:
        _report_unrecognized_pending_contents(context, pending, current_output)


def _report_identity_change(
    context: _Reconciliation,
    pending: _PendingRewrite,
    current_output: tuple[str, AtomicWriteIdentity],
) -> None:
    """Report a replacement-identity change recorded against pending text."""
    if current_output[0] != pending.pending_text:
        _report_unrecognized_pending_contents(context, pending, current_output)
        return
    context.failure.add_note(
        "Manifest replacement identity changed while a rewrite was "
        f"pending; leaving its current contents in place: {pending.path}"
    )


def _report_unrecognized_pending_contents(
    context: _Reconciliation,
    pending: _PendingRewrite,
    current_output: tuple[str, AtomicWriteIdentity],
) -> None:
    """Attach a diagnostic when pending contents differ from both known texts."""
    current_text = current_output[0]
    baseline_text = context.stage.rollback_baselines.get(pending.path)
    completed_output = (
        context.stage.written_manifest_contents.get(pending.path),
        context.stage.written_manifest_identities.get(pending.path),
    )
    if current_text == baseline_text:
        return
    if current_output == completed_output:
        return
    context.failure.add_note(
        "Manifest changed while a rewrite was pending; leaving its "
        f"current contents in place: {pending.path}"
    )
