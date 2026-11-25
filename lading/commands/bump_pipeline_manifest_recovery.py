"""Register and restore manifest writes performed by the bump pipeline."""

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteWriteError,
)
from lading.commands.bump_toml_files import write_atomic_text

if typ.TYPE_CHECKING:
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


def manifest_write_recorder(
    rewrite_stage: _ManifestRewriteStage,
) -> cabc.Callable[[Path, str, str, Path], None]:
    """Return the pre-replacement callback for one atomic manifest write.

    The caller must invoke this callback after preparing the replacement and
    immediately before calling the atomic writer. It registers the baseline and
    pending output; it does not claim that the replacement completed.

    Returns
    -------
    Callable
        A callback accepting the manifest path, original text, planned text,
        and resolved write destination.
    """

    def record_write(
        manifest_path: Path,
        original_text: str,
        written_text: str,
        write_destination: Path,
    ) -> None:
        """Keep the rollback baseline and expected output for one write."""
        rewrite_stage.rollback_baselines.setdefault(manifest_path, original_text)
        rewrite_stage.pending_manifest_contents[manifest_path] = written_text
        rewrite_stage.written_manifest_destinations[manifest_path] = write_destination

    return record_write


def manifest_write_completion_recorder(
    rewrite_stage: _ManifestRewriteStage,
) -> cabc.Callable[[Path], None]:
    """Return the post-replacement callback that confirms a completed write."""

    def record_completion(manifest_path: Path) -> None:
        """Promote the pending output only after the atomic writer returns."""
        if manifest_path in rewrite_stage.pending_manifest_contents:
            rewrite_stage.written_manifest_contents[manifest_path] = (
                rewrite_stage.pending_manifest_contents.pop(manifest_path)
            )

    return record_completion


def validate_member_manifest_before_update(
    manifest_path: Path,
    rewrite_stage: _ManifestRewriteStage | None,
) -> None:
    """Reject edits or destination changes after an earlier bump write."""
    if rewrite_stage is None:
        return
    previously_written_text = rewrite_stage.written_manifest_contents.get(manifest_path)
    if previously_written_text is None:
        return
    _validate_member_manifest_destination(manifest_path, rewrite_stage)
    _validate_member_manifest_contents(manifest_path, previously_written_text)


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
) -> None:
    """Reject content changes made after an earlier bump pass wrote the file."""
    try:
        current_text = manifest_path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeError) as exc:
        message = (
            f"Could not verify manifest after an earlier bump pass wrote it: "
            f"{manifest_path}: {exc}"
        )
        raise ManifestRewriteError(message, failed_path=manifest_path) from exc
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
    """Record only completed writes and the single pending atomic write.

    Completed fixture writes are known from their post-write callbacks. The
    pending write is inspected because interruption may have arrived after the
    atomic replace but before that callback. Unattempted plans are never read or
    attributed to this bump.
    """
    rewrite_paths = {plan.path for plan in stage.plans}
    changed_manifests.update(
        rewrite_paths.intersection(stage.written_manifest_contents)
    )
    for manifest_path, pending_text in tuple(stage.pending_manifest_contents.items()):
        try:
            current_text = manifest_path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeError) as exc:
            interruption.add_note(
                f"Could not inspect manifest rewrite target {manifest_path} "
                f"during interruption recovery: {exc}"
            )
            continue
        if current_text != pending_text:
            continue
        stage.written_manifest_contents[manifest_path] = (
            stage.pending_manifest_contents.pop(manifest_path)
        )
        changed_manifests.add(manifest_path)


def restore_changed_manifests(
    stage: _ManifestRewriteStage,
    changed_manifests: cabc.Collection[Path],
    failure: BaseException,
    *,
    workspace_root: Path,
) -> ManifestRewriteWriteError | None:
    """Restore manifests that still contain one of this bump's outputs."""
    original_text_by_path = dict(stage.workspace_manifest_snapshots)
    original_text_by_path.update(stage.rollback_baselines)
    failures: list[tuple[Path, str]] = []
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
    return _manifest_rollback_error(failures, failure)


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
        current_text = write_destination.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeError) as exc:
        return f"could not read before rollback: {exc}"
    if current_text == original_text:
        return None
    expected_text = stage.written_manifest_contents.get(manifest_path)
    if current_text != expected_text:
        return (
            "contents changed after this bump wrote the manifest; "
            "left the current contents in place"
        )
    try:
        write_atomic_text(
            write_destination,
            original_text,
            workspace_root=workspace_root,
            expected_current_text=current_text,
        )
    except (OSError, UnicodeError) as exc:
        return f"could not restore original text: {exc}"
    return None
