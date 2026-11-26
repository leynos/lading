"""Register and restore manifest writes performed by the bump pipeline."""

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.commands import (
    bump_pipeline_manifest_restore,
    bump_toml_write_options,
)
from lading.commands.bump_manifest_rewrite_model import ManifestRewriteError
from lading.commands.bump_pipeline_pending_reconciliation import (
    reconcile_pending_manifest_rewrites,
)
from lading.commands.bump_toml_bounded_filesystem import _read_bounded_regular_text

if typ.TYPE_CHECKING:
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


def manifest_write_recorder(
    rewrite_stage: _ManifestRewriteStage,
) -> cabc.Callable[[Path, str, str, Path], bump_toml_write_options.AtomicWriteStatus]:
    """Return the pre-replacement callback for one atomic manifest write.

    The caller must invoke this callback after preparing the replacement and
    immediately before calling the atomic writer. It registers the baseline and
    pending output; it does not claim that the replacement completed.

    Parameters
    ----------
    rewrite_stage
        Stage whose baseline, pending-output and resolved-destination maps are
        populated by the returned callback.

    Returns
    -------
    Callable
        A callback accepting the manifest path, original text, planned text,
        and resolved write destination, then returning its replacement status.

    Notes
    -----
    The callback key must be the same resolved manifest ``Path`` used by every
    later recovery callback. Invoke it after the replacement has been prepared
    and immediately before the atomic writer. It preserves the first source in
    ``rollback_baselines`` and records the pending output in
    ``pending_manifest_contents`` plus the destination in
    ``written_manifest_destinations``.
    """

    def record_write(
        manifest_path: Path,
        original_text: str,
        written_text: str,
        write_destination: Path,
    ) -> bump_toml_write_options.AtomicWriteStatus:
        """Keep recovery state and create a status for one atomic replacement."""
        rewrite_stage.rollback_baselines.setdefault(manifest_path, original_text)
        rewrite_stage.pending_manifest_contents[manifest_path] = written_text
        rewrite_stage.written_manifest_destinations[manifest_path] = write_destination
        status = bump_toml_write_options.AtomicWriteStatus()
        rewrite_stage.pending_write_statuses[manifest_path] = status
        return status

    return record_write


def manifest_write_completion_recorder(
    rewrite_stage: _ManifestRewriteStage,
) -> cabc.Callable[[Path], None]:
    """Return the post-replacement callback that confirms a completed write.

    Parameters
    ----------
    rewrite_stage
        Stage whose pending and completed output maps are updated.

    Returns
    -------
    Callable
        A callback accepting the same manifest path identity passed to
        :func:`manifest_write_recorder`.

    Notes
    -----
    Invoke the callback immediately after the atomic writer returns
    successfully. It copies the pending text to ``written_manifest_contents``
    before removing it from ``pending_manifest_contents`` so an interruption
    cannot erase the only recovery record.
    """

    def record_completion(manifest_path: Path) -> None:
        """Promote the pending output only after the atomic writer returns."""
        if manifest_path in rewrite_stage.pending_manifest_contents:
            pending_text = rewrite_stage.pending_manifest_contents[manifest_path]
            rewrite_stage.written_manifest_contents[manifest_path] = pending_text
            status = rewrite_stage.pending_write_statuses.get(manifest_path)
            if status is not None and status.replacement_identity is not None:
                rewrite_stage.written_manifest_identities[manifest_path] = (
                    status.replacement_identity
                )
            rewrite_stage.pending_manifest_contents.pop(manifest_path)
            rewrite_stage.pending_write_statuses.pop(manifest_path, None)

    return record_completion


def validate_member_manifest_before_update(
    manifest_path: Path,
    rewrite_stage: _ManifestRewriteStage | None,
) -> None:
    """Reject edits or destination changes after an earlier bump write.

    Parameters
    ----------
    manifest_path
        Manifest identity used as the key by the earlier write callbacks.
    rewrite_stage
        Recovery stage whose completed-output and destination maps are checked.
        ``None`` means no earlier rewrite stage was configured.

    Notes
    -----
    This query mutates no stage mappings. When the path has a completed write,
    it verifies its resolved destination, replacement identity and exact UTF-8
    contents. Validation either succeeds or raises ``ManifestRewriteError``.
    """
    if rewrite_stage is None:
        return
    previously_written_text = rewrite_stage.written_manifest_contents.get(manifest_path)
    if previously_written_text is None:
        return
    _validate_member_manifest_destination(manifest_path, rewrite_stage)
    _validate_member_manifest_contents(
        manifest_path,
        previously_written_text,
        rewrite_stage.written_manifest_identities.get(manifest_path),
        rewrite_stage.workspace_root or manifest_path.parent,
    )


def _validate_member_manifest_destination(
    manifest_path: Path,
    rewrite_stage: _ManifestRewriteStage,
) -> None:
    """Verify that the manifest still resolves to its recorded destination."""
    previous_destination = rewrite_stage.written_manifest_destinations.get(
        manifest_path
    )
    if previous_destination is None:
        return
    try:
        current_destination = manifest_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        message = (
            f"Could not verify manifest destination after an earlier bump pass "
            f"wrote it: {manifest_path}: {exc}"
        )
        raise ManifestRewriteError(message, failed_path=manifest_path) from exc
    if current_destination != previous_destination:
        message = (
            f"Manifest destination changed after an earlier bump pass wrote it: "
            f"{manifest_path}"
        )
        raise ManifestRewriteError(message, failed_path=manifest_path)


def _validate_member_manifest_contents(
    manifest_path: Path,
    previously_written_text: str,
    previously_written_identity: bump_toml_write_options.AtomicWriteIdentity | None,
    workspace_root: Path,
) -> None:
    """Reject replacement or content changes since the earlier bump write."""
    try:
        current_text, current_identity = _read_bounded_regular_text(
            manifest_path, workspace_root
        )
    except (OSError, UnicodeError) as exc:
        message = (
            f"Could not verify manifest after an earlier bump pass wrote it: "
            f"{manifest_path}: {exc}"
        )
        raise ManifestRewriteError(message, failed_path=manifest_path) from exc
    if previously_written_identity is None or current_identity != (
        previously_written_identity
    ):
        message = (
            "Manifest replacement identity changed after an earlier bump pass "
            f"wrote it: {manifest_path}"
        )
        raise ManifestRewriteError(message, failed_path=manifest_path)
    if current_text != previously_written_text:
        message = (
            f"Manifest changed after an earlier bump pass wrote it: {manifest_path}"
        )
        raise ManifestRewriteError(message, failed_path=manifest_path)


def record_completed_rewrites_for_interruption(
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
    interruption: BaseException,
) -> None:
    """Record completed writes and inspect the pending atomic write.

    Parameters
    ----------
    stage
        Rewrite stage whose completed and pending output maps are reconciled.
    changed_manifests
        Mutable path set to which confirmed completed rewrite paths are added.
    interruption
        Active ``BaseException`` that recovery will re-raise after rollback.

    Notes
    -----
    Completed fixture writes are known from their post-write callbacks. The
    pending write is inspected because an exception may have arrived after the
    atomic replace but before that callback. Unattempted plans are never read or
    attributed to this bump. Reconciliation may move matching entries from
    ``pending_manifest_contents`` to ``written_manifest_contents`` and adds
    confirmed paths to ``changed_manifests`` in place.
    """
    rewrite_paths = {plan.path for plan in stage.plans}
    changed_manifests.update(
        rewrite_paths.intersection(stage.written_manifest_contents)
    )
    reconcile_pending_manifest_rewrites(stage, changed_manifests, interruption)


def reconcile_manifest_writes_for_failure(
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
    failure: BaseException,
) -> None:
    """Reconcile pending writes after errors and completed writes on interrupt.

    Parameters
    ----------
    stage
        Stage containing the pending and completed output maps.
    changed_manifests
        Mutable path set updated with confirmed writes.
    failure
        Failure being recovered. Ordinary ``Exception`` values trigger a read
        of pending outputs; other ``BaseException`` values use callback-confirmed
        writes and inspect the active pending write.

    Notes
    -----
    Errors reconcile pending reads; interrupts trust callbacks and inspect the
    active write.
    """
    if isinstance(failure, Exception):
        reconcile_pending_manifest_rewrites(stage, changed_manifests, failure)
        return
    record_completed_rewrites_for_interruption(stage, changed_manifests, failure)


def restore_changed_manifests(
    stage: _ManifestRewriteStage,
    changed_manifests: cabc.Collection[Path],
    failure: BaseException,
    *,
    workspace_root: Path,
) -> bump_pipeline_manifest_restore.ManifestRewriteWriteError | None:
    """Restore manifests that still contain one of this bump's outputs.

    Parameters
    ----------
    stage
        Stage containing rollback baselines and completed output identities.
    changed_manifests
        Paths confirmed or conservatively attributed to this bump.
    failure
        Primary error to which restoration details are attached.
    workspace_root
        Root used to confine each bounded restoration write.

    Notes
    -----
    The restoration adapter reads ``workspace_manifest_snapshots``,
    ``rollback_baselines``, ``written_manifest_contents`` and
    ``written_manifest_destinations`` as evidence. It does not clear or mutate
    those mappings.

    Returns
    -------
    ManifestRewriteWriteError or None
        An aggregate restoration error when any candidate could not safely be
        restored; otherwise ``None``.
    """
    return bump_pipeline_manifest_restore.restore_changed_manifests(
        stage,
        changed_manifests,
        failure,
        workspace_root=workspace_root,
    )
