"""Restore manifest files after a failed bump pipeline.

This adapter is called only from the bump recovery coordinator. It uses the
coordinator's recorded outputs to restore unchanged writes through the bounded
workspace writer; it does not discover manifests or choose recovery policy.
"""

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteWriteError,
)
from lading.commands.bump_toml_bounded_filesystem import _read_bounded_regular_text
from lading.commands.bump_toml_files import write_atomic_text
from lading.commands.bump_toml_write_options import (
    AtomicWriteIdentity,
    AtomicWriteOptions,
    AtomicWriteStatus,
)

if typ.TYPE_CHECKING:
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


def restore_changed_manifests(
    stage: _ManifestRewriteStage,
    changed_manifests: cabc.Collection[Path],
    failure: BaseException,
    *,
    workspace_root: Path,
) -> ManifestRewriteWriteError | None:
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

    Returns
    -------
    ManifestRewriteWriteError or None
        An aggregate restoration error when any candidate could not safely be
        restored; otherwise ``None``. The stage maps are read as evidence and
        are not cleared by this function.
    """
    original_text_by_path = dict(stage.workspace_manifest_snapshots)
    original_text_by_path.update(stage.rollback_baselines)
    non_member_paths = {plan.path for plan in stage.plans}
    registered_manifests = set(stage.written_manifest_contents) - non_member_paths
    rollback_candidates = (set(changed_manifests) | registered_manifests).intersection(
        original_text_by_path
    )
    failures = _restore_rollback_candidates(
        rollback_candidates,
        original_text_by_path,
        stage,
        workspace_root,
    )
    rollback_error = _manifest_rollback_error(failures, failure)
    remaining_fixtures = _remaining_fixture_rewrites(stage, workspace_root)
    if remaining_fixtures:
        paths = ", ".join(str(path) for path in remaining_fixtures)
        failure.add_note(
            "Completed non-member manifest rewrite paths are not back to their "
            f"pre-bump contents; inspect these paths before retrying: {paths}"
        )
    return rollback_error


def _remaining_fixture_rewrites(
    stage: _ManifestRewriteStage,
    workspace_root: Path,
) -> tuple[Path, ...]:
    """Return completed fixture writes that no longer match their baselines."""
    completed_paths = {plan.path for plan in stage.plans}.intersection(
        stage.written_manifest_contents
    )
    remaining: list[Path] = []
    for manifest_path in sorted(completed_paths, key=str):
        baseline = stage.rollback_baselines.get(manifest_path)
        if baseline is None:
            remaining.append(manifest_path)
            continue
        destination = stage.written_manifest_destinations.get(
            manifest_path, manifest_path
        )
        try:
            current_text, _identity = _read_bounded_regular_text(
                destination, workspace_root
            )
        except OSError, UnicodeError:
            remaining.append(manifest_path)
            continue
        if current_text != baseline:
            remaining.append(manifest_path)
    return tuple(remaining)


def _restore_rollback_candidates(
    rollback_candidates: cabc.Collection[Path],
    original_text_by_path: cabc.Mapping[Path, str],
    stage: _ManifestRewriteStage,
    workspace_root: Path,
) -> list[tuple[Path, str]]:
    """Restore each candidate and collect files that could not be restored."""
    failures: list[tuple[Path, str]] = []
    for manifest_path in sorted(rollback_candidates, key=str, reverse=True):
        detail = _restore_one_manifest(
            manifest_path,
            original_text_by_path[manifest_path],
            stage,
            workspace_root,
        )
        if detail is not None:
            failures.append((manifest_path, detail))
    return failures


def _manifest_rollback_error(
    failures: list[tuple[Path, str]],
    failure: BaseException,
) -> ManifestRewriteWriteError | None:
    """Describe restoration failures while retaining rewrite progress."""
    if not failures:
        return None
    failed_paths = ", ".join(str(path) for path, _error in failures)
    failure_details = "; ".join(f"{path}: {detail}" for path, detail in failures)
    failure_summary = str(failure) or type(failure).__name__
    message = (
        f"{failure_summary}; manifest rollback failed for "
        f"{failed_paths}: {failure_details}"
    )
    completed_writes = (
        failure.completed_writes if isinstance(failure, ManifestRewriteError) else 0
    )
    planned_writes = (
        failure.planned_writes if isinstance(failure, ManifestRewriteError) else 0
    )
    return ManifestRewriteWriteError(
        message,
        completed_writes=completed_writes,
        planned_writes=planned_writes,
        failed_path=failures[0][0],
    )


def _restore_one_manifest(
    manifest_path: Path,
    original_text: str,
    stage: _ManifestRewriteStage,
    workspace_root: Path,
) -> str | None:
    """Restore one unchanged bump output and return any recovery failure."""
    write_destination = stage.written_manifest_destinations.get(
        manifest_path, manifest_path
    )
    try:
        current_text, current_identity = _read_bounded_regular_text(
            write_destination, workspace_root
        )
    except (OSError, UnicodeError) as exc:
        return f"could not read before rollback: {exc}"
    if current_text == original_text:
        return None
    expected_identity = stage.written_manifest_identities.get(manifest_path)
    conflict = _rollback_conflict_detail(
        manifest_path,
        current_text,
        current_identity,
        stage,
    )
    if conflict is not None:
        return conflict
    write_status = AtomicWriteStatus()
    try:
        write_atomic_text(
            write_destination,
            original_text,
            AtomicWriteOptions(
                workspace_root=workspace_root,
                expected_current_text=current_text,
                expected_current_identity=expected_identity,
                status=write_status,
            ),
        )
    except (OSError, UnicodeError) as exc:
        if write_status.replacement_completed:
            return (
                "rollback replacement completed, but a later write operation "
                f"failed: {exc}"
            )
        return f"could not restore original text: {exc}"
    return None


def _rollback_conflict_detail(
    manifest_path: Path,
    current_text: str,
    current_identity: AtomicWriteIdentity,
    stage: _ManifestRewriteStage,
) -> str | None:
    """Describe why current contents are no longer this bump's output."""
    expected_identity = stage.written_manifest_identities.get(manifest_path)
    if expected_identity is None or current_identity != expected_identity:
        return (
            "replacement identity changed after this bump wrote the manifest; "
            "left the current contents in place"
        )
    if current_text != stage.written_manifest_contents.get(manifest_path):
        return (
            "contents changed after this bump wrote the manifest; "
            "left the current contents in place"
        )
    return None
