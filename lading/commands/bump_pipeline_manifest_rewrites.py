"""Coordinate configured non-member manifest rewrite stages.

The pipeline plans these rewrites before any writes and applies them after
workspace and member manifests. A failed manifest stage restores changed
manifests so a retry still has the original crate versions.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

from lading.commands import (
    bump_manifest_rewrite_application,
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrite_metrics,
    bump_manifest_rewrites,
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_sources,
    bump_toml_write_options,
    bump_workspace_write_lock,
)
from lading.commands.bump_manifest_rewrite_metrics import (
    ManifestRewriteMetricLabels,
)
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteWriteError,
)

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifests import _BumpContext

_log = logging.getLogger(__name__)


@dc.dataclass(slots=True)
class _ManifestRewriteStage:
    """Configured rewrite plans, timing, and workspace recovery snapshots."""

    is_configured: bool = False
    workspace_root: Path | None = None
    plans: tuple[bump_manifest_rewrites.ManifestRewritePlan, ...] = ()
    planning_duration_seconds: float = 0.0
    workspace_manifest_snapshots: tuple[tuple[Path, str], ...] = ()
    pre_bump_versions: dict[str, str] = dc.field(default_factory=dict)
    expected_version_values: dict[Path, tuple[tuple[tuple[str, ...], str], ...]] = (
        dc.field(default_factory=dict)
    )
    expected_manifest_contents: dict[Path, str] = dc.field(default_factory=dict)
    written_manifest_contents: dict[Path, str] = dc.field(default_factory=dict)
    written_manifest_identities: dict[
        Path, bump_toml_write_options.AtomicWriteIdentity
    ] = dc.field(default_factory=dict)
    pending_manifest_contents: dict[Path, str] = dc.field(default_factory=dict)
    pending_write_statuses: dict[Path, bump_toml_write_options.AtomicWriteStatus] = (
        dc.field(default_factory=dict)
    )
    written_manifest_destinations: dict[Path, Path] = dc.field(default_factory=dict)
    rollback_baselines: dict[Path, str] = dc.field(default_factory=dict)


def _plan_non_member_manifest_rewrites(
    context: _BumpContext,
    target_version: str,
) -> _ManifestRewriteStage:
    """Plan rewrites and snapshot workspace manifests before the first write."""
    rewrite_groups = context.configuration.bump.manifest_rewrites
    if not rewrite_groups:
        return _ManifestRewriteStage()
    clock = context.base_options.clock
    planning_started_at = clock()
    member_manifest_paths = tuple(
        crate.manifest_path for crate in context.workspace.crates
    )
    try:
        workspace_manifest_snapshots = (
            bump_pipeline_manifest_sources._read_workspace_manifest_snapshots(
                context.workspace_manifest, member_manifest_paths
            )
        )
        pre_bump_versions = (
            bump_pipeline_manifest_sources._pre_bump_versions_from_snapshots(
                context, workspace_manifest_snapshots
            )
        )
        sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
            context.root_path, rewrite_groups, member_manifest_paths
        )
        plans = bump_manifest_rewrites.plan_manifest_rewrites(
            sources,
            bump_manifest_rewrites.ManifestRewriteVersions(
                updated_crate_names=context.updated_crate_names,
                pre_bump_versions=pre_bump_versions,
                target_version=target_version,
            ),
        )
    except ManifestRewriteError:
        bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
            clock() - planning_started_at,
            ManifestRewriteMetricLabels(
                phase="planning",
                outcome="failed",
                dry_run=context.base_options.dry_run,
                error_category="planning",
            ),
        )
        raise
    planning_duration = clock() - planning_started_at
    bump_manifest_rewrite_metrics.record_manifest_rewrite_planned(
        dry_run=context.base_options.dry_run
    )
    return _ManifestRewriteStage(
        is_configured=True,
        workspace_root=context.root_path,
        plans=plans,
        planning_duration_seconds=planning_duration,
        workspace_manifest_snapshots=workspace_manifest_snapshots,
        pre_bump_versions=pre_bump_versions,
        expected_version_values=(
            bump_pipeline_manifest_sources._expected_version_values_from_snapshots(
                workspace_manifest_snapshots
            )
        ),
    )


def _apply_non_member_manifest_rewrites(
    context: _BumpContext,
    target_version: str,
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
) -> None:
    """Apply planned fixture rewrites and restore workspace versions on failure."""
    if not stage.is_configured:
        return
    dry_run = context.base_options.dry_run
    clock = context.base_options.clock
    _log.debug(
        "Non-member manifest rewrites: %d planned manifest(s), dry_run=%s",
        len(stage.plans),
        dry_run,
    )
    application_started_at = clock()
    try:
        rewritten_paths = _apply_planned_rewrites(
            context, target_version, stage, dry_run=dry_run
        )
    except BaseException as rewrite_error:
        if _should_recover_manifest_rewrite_failure(rewrite_error, dry_run=dry_run):
            recovery_error = _recover_failed_manifest_rewrite_application(
                context,
                stage,
                changed_manifests,
                rewrite_error,
            )
            _record_manifest_rewrite_metric(
                stage,
                application_started_at,
                clock,
                _application_metrics_labels(
                    dry_run=dry_run,
                    failed=True,
                    has_plans=bool(stage.plans),
                ),
            )
            if recovery_error is not None:
                raise recovery_error from rewrite_error
        raise
    # The resolver already canonicalized these paths; resolving after a write
    # could follow a replaced symlink and report a different manifest.
    changed_manifests.update(rewritten_paths)
    _record_manifest_rewrite_metric(
        stage,
        application_started_at,
        clock,
        _application_metrics_labels(
            dry_run=dry_run,
            failed=False,
            has_plans=bool(stage.plans),
        ),
    )


def _application_metrics_labels(
    *,
    dry_run: bool,
    failed: bool,
    has_plans: bool,
) -> ManifestRewriteMetricLabels:
    """Return outcome labels for an applied or failed rewrite stage."""
    if failed:
        outcome, error_category = "failed", "write"
    elif dry_run or not has_plans:
        outcome, error_category = "skipped", "none"
    else:
        outcome, error_category = "applied", "none"
    return ManifestRewriteMetricLabels(
        phase="application",
        outcome=outcome,
        dry_run=dry_run,
        error_category=error_category,
    )


def _record_manifest_rewrite_metric(
    stage: _ManifestRewriteStage,
    application_started_at: float,
    clock: cabc.Callable[[], float],
    labels: ManifestRewriteMetricLabels,
) -> None:
    """Record one rewrite duration with its outcome labels."""
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        stage.planning_duration_seconds + clock() - application_started_at,
        labels,
    )


def _apply_planned_rewrites(
    context: _BumpContext,
    target_version: str,
    stage: _ManifestRewriteStage,
    *,
    dry_run: bool,
) -> tuple[Path, ...]:
    """Apply cached plans through the boundary their configuration requires."""
    if dry_run or not stage.plans:
        return _apply_rewrite_plans(context, stage, dry_run=dry_run)
    return _apply_rewrites_under_workspace_lock(context, target_version, stage)


def _apply_rewrite_plans(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    *,
    dry_run: bool,
) -> tuple[Path, ...]:
    """Apply cached plans with the write callbacks owned by this stage."""
    return bump_manifest_rewrite_application.apply_manifest_rewrites(
        stage.plans,
        workspace_root=stage.workspace_root or context.root_path,
        dry_run=dry_run,
        write_callbacks=(
            bump_manifest_rewrite_application._ManifestRewriteWriteCallbacks(
                before_write=(
                    bump_pipeline_manifest_recovery.manifest_write_recorder(stage)
                ),
                after_write=(
                    bump_pipeline_manifest_recovery.manifest_write_completion_recorder(
                        stage
                    )
                ),
            )
        ),
    )


def _apply_rewrites_under_workspace_lock(
    context: _BumpContext,
    target_version: str,
    stage: _ManifestRewriteStage,
) -> tuple[Path, ...]:
    """Revalidate source versions and write fixtures under one workspace lock."""
    workspace_root = stage.workspace_root or context.root_path
    try:
        with bump_workspace_write_lock.workspace_write_lock(workspace_root):
            bump_pipeline_manifest_sources._validate_post_bump_versions(
                context, stage, target_version
            )
            return _apply_rewrite_plans(context, stage, dry_run=False)
    except OSError as exc:
        failed_path = stage.plans[0].path if stage.plans else None
        message = (
            "Could not coordinate configured manifest rewrites with workspace "
            f"writes: {exc}"
        )
        raise ManifestRewriteError(message, failed_path=failed_path) from exc


def _should_recover_manifest_rewrite_failure(
    rewrite_error: BaseException,
    *,
    dry_run: bool,
) -> bool:
    """Select handled write failures while preserving dry-run interrupts."""
    return isinstance(rewrite_error, ManifestRewriteError) or (
        not isinstance(rewrite_error, Exception) and not dry_run
    )


def _recover_failed_manifest_rewrite_application(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
    rewrite_error: BaseException,
) -> ManifestRewriteWriteError | None:
    """Restore outputs after a handled manifest rewrite failure."""
    interrupted = not isinstance(rewrite_error, Exception)
    if not context.base_options.dry_run:
        bump_pipeline_manifest_recovery.reconcile_manifest_writes_for_failure(
            stage, changed_manifests, rewrite_error
        )
    recovery_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        changed_manifests,
        rewrite_error,
        workspace_root=context.root_path,
    )
    if recovery_error is not None and interrupted:
        rewrite_error.add_note(str(recovery_error))
        return None
    if recovery_error is not None:
        for note in getattr(rewrite_error, "__notes__", ()):
            recovery_error.add_note(note)
    return recovery_error
