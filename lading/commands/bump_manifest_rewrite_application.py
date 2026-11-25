"""Apply already-planned edits to configured non-member Cargo manifests."""

import collections.abc as cabc
import dataclasses as dc
from pathlib import Path

from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewritePlan,
    ManifestRewriteWriteError,
)
from lading.commands.bump_toml_files import write_atomic_text


@dc.dataclass(frozen=True, slots=True)
class _ManifestRewriteWriteCallbacks:
    """Callbacks that journal the start and completion of a rewrite write."""

    before_write: cabc.Callable[[Path, str, str, Path], None] | None = None
    after_write: cabc.Callable[[Path], None] | None = None


@dc.dataclass(frozen=True, slots=True)
class _ManifestRewriteProgress:
    """Completed and total writes used in interruption error messages."""

    completed: int
    planned: int


def apply_manifest_rewrites(
    plans: cabc.Sequence[ManifestRewritePlan],
    *,
    workspace_root: Path,
    dry_run: bool,
    write_callbacks: _ManifestRewriteWriteCallbacks | None = None,
) -> tuple[Path, ...]:
    """Write each planned manifest atomically unless this is a dry run.

    Parameters
    ----------
    plans : Sequence[ManifestRewritePlan]
        Changed non-member manifest plans returned by the rewrite planner.
    workspace_root : Path
        Root that bounds every destination at the atomic write boundary.
    dry_run : bool
        When true, report planned paths without writing any files.
    write_callbacks : _ManifestRewriteWriteCallbacks or None, optional
        Optional callbacks that journal the replacement before it starts and
        confirm completion after the atomic writer returns.

    Returns
    -------
    tuple[Path, ...]
        Paths represented by the plans, including during dry runs.

    Notes
    -----
    The atomic writer compares the exact source text immediately before
    replacement under a workspace-wide advisory lock shared by Lading writes.
    Other tools may ignore the lock and race that final comparison.

    Each atomic write protects one manifest. If a later write fails, earlier
    completed writes remain in place, so the sequence is not atomic as a whole.
    """
    if dry_run:
        return tuple(plan.path for plan in plans)
    for completed_writes, plan in enumerate(plans):
        _apply_manifest_rewrite(
            plan,
            workspace_root,
            _ManifestRewriteProgress(completed_writes, len(plans)),
            write_callbacks,
        )
    return tuple(plan.path for plan in plans)


def _apply_manifest_rewrite(
    plan: ManifestRewritePlan,
    workspace_root: Path,
    progress: _ManifestRewriteProgress,
    write_callbacks: _ManifestRewriteWriteCallbacks | None,
) -> None:
    """Check one plan's source before atomically persisting its replacement."""
    _validate_plan_destination(plan, progress)
    current_text = _read_current_text(plan, progress)
    if current_text != plan.original_text:
        message = f"Manifest rewrite target changed after planning: {plan.path}"
        raise ManifestRewriteError(
            message,
            completed_writes=progress.completed,
            planned_writes=progress.planned,
            failed_path=plan.path,
        )
    _write_manifest_plan(plan, workspace_root, progress, write_callbacks)


def _validate_plan_destination(
    plan: ManifestRewritePlan,
    progress: _ManifestRewriteProgress,
) -> None:
    """Reject a destination that no longer resolves to its planned path."""
    try:
        resolved_path = plan.path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        message = (
            f"Could not resolve manifest rewrite target {plan.path} before writing: "
            f"{exc}"
        )
        raise ManifestRewriteError(
            message,
            completed_writes=progress.completed,
            planned_writes=progress.planned,
            failed_path=plan.path,
        ) from exc
    if resolved_path != plan.path:
        message = (
            f"Manifest rewrite target no longer resolves to its planned path: "
            f"{plan.path}"
        )
        raise ManifestRewriteError(
            message,
            completed_writes=progress.completed,
            planned_writes=progress.planned,
            failed_path=plan.path,
        )


def _read_current_text(
    plan: ManifestRewritePlan,
    progress: _ManifestRewriteProgress,
) -> str:
    """Read a plan source or raise a progress-aware rewrite error."""
    try:
        return plan.path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeError) as exc:
        message = (
            f"Could not read manifest rewrite target {plan.path} before writing: {exc}"
        )
        raise ManifestRewriteError(
            message,
            completed_writes=progress.completed,
            planned_writes=progress.planned,
            failed_path=plan.path,
        ) from exc


def _write_manifest_plan(
    plan: ManifestRewritePlan,
    workspace_root: Path,
    progress: _ManifestRewriteProgress,
    write_callbacks: _ManifestRewriteWriteCallbacks | None,
) -> None:
    """Atomically write a plan or raise a progress-aware rewrite error."""
    try:
        if write_callbacks is not None and write_callbacks.before_write is not None:
            write_callbacks.before_write(
                plan.path, plan.original_text, plan.new_text, plan.path
            )
        write_atomic_text(
            plan.path,
            plan.new_text,
            workspace_root=workspace_root,
            expected_current_text=plan.original_text,
        )
        if write_callbacks is not None and write_callbacks.after_write is not None:
            write_callbacks.after_write(plan.path)
    except (OSError, UnicodeError) as exc:
        message = (
            f"Could not write manifest rewrite target {plan.path} after "
            f"{progress.completed} of {progress.planned} write(s): {exc}"
        )
        raise ManifestRewriteWriteError(
            message,
            completed_writes=progress.completed,
            planned_writes=progress.planned,
            failed_path=plan.path,
        ) from exc
