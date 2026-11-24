"""Coordinate configured non-member manifest rewrite stages.

The pipeline plans these rewrites before any writes and applies them after
workspace and member manifests. A failed application restores changed
workspace manifests so a retry still has the original crate versions.
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
    bump_toml_files,
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


@dc.dataclass(frozen=True, slots=True)
class _ManifestRewriteStage:
    """Configured rewrite plans, timing, and workspace recovery snapshots."""

    is_configured: bool = False
    plans: tuple[bump_manifest_rewrites.ManifestRewritePlan, ...] = ()
    planning_duration_seconds: float = 0.0
    workspace_manifest_snapshots: tuple[tuple[Path, str], ...] = ()


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
    pre_bump_versions = {
        crate.name: crate.version for crate in context.workspace.crates
    }
    member_manifest_paths = tuple(
        crate.manifest_path for crate in context.workspace.crates
    )
    try:
        workspace_manifest_snapshots = _read_workspace_manifest_snapshots(
            context.workspace_manifest, member_manifest_paths
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
        plans=plans,
        planning_duration_seconds=planning_duration,
        workspace_manifest_snapshots=workspace_manifest_snapshots,
    )


def _read_workspace_manifest_snapshots(
    workspace_manifest: Path,
    member_manifest_paths: cabc.Collection[Path],
) -> tuple[tuple[Path, str], ...]:
    """Read original workspace and member text for failed-stage recovery."""
    paths = dict.fromkeys((workspace_manifest, *member_manifest_paths))
    snapshots: list[tuple[Path, str]] = []
    for manifest_path in paths:
        try:
            content = manifest_path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeError) as exc:
            message = (
                "Could not snapshot workspace manifest for rewrite recovery "
                f"{manifest_path}: {exc}"
            )
            raise ManifestRewriteError(message, failed_path=manifest_path) from exc
        snapshots.append((manifest_path, content))
    return tuple(snapshots)


def _apply_non_member_manifest_rewrites(
    context: _BumpContext,
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
        rewritten_paths = bump_manifest_rewrite_application.apply_manifest_rewrites(
            stage.plans, dry_run=dry_run
        )
    except ManifestRewriteError as rewrite_error:
        recovery_error = _restore_changed_workspace_manifests(
            stage.workspace_manifest_snapshots,
            changed_manifests,
            rewrite_error,
        )
        bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
            stage.planning_duration_seconds + clock() - application_started_at,
            ManifestRewriteMetricLabels(
                phase="application",
                outcome="failed",
                dry_run=dry_run,
                error_category="write",
            ),
        )
        if recovery_error is not None:
            raise recovery_error from rewrite_error
        raise
    # The resolver already canonicalized these paths; resolving after a write
    # could follow a replaced symlink and report a different manifest.
    changed_manifests.update(rewritten_paths)
    outcome = "skipped" if dry_run or not stage.plans else "applied"
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        stage.planning_duration_seconds + clock() - application_started_at,
        ManifestRewriteMetricLabels(
            phase="application",
            outcome=outcome,
            dry_run=dry_run,
            error_category="none",
        ),
    )


def _restore_changed_workspace_manifests(
    snapshots: cabc.Sequence[tuple[Path, str]],
    changed_manifests: cabc.Collection[Path],
    rewrite_error: ManifestRewriteError,
) -> ManifestRewriteWriteError | None:
    """Restore pre-bump versions or describe any rollback write failures."""
    original_text_by_path = dict(snapshots)
    failures: list[tuple[Path, OSError | UnicodeError]] = []
    changed_workspace_manifests = sorted(
        set(changed_manifests).intersection(original_text_by_path),
        key=str,
        reverse=True,
    )
    for manifest_path in changed_workspace_manifests:
        try:
            bump_toml_files.write_atomic_text(
                manifest_path, original_text_by_path[manifest_path]
            )
        except (OSError, UnicodeError) as exc:
            failures.append((manifest_path, exc))
    if not failures:
        return None
    failed_paths = ", ".join(str(path) for path, _error in failures)
    failure_details = "; ".join(str(error) for _path, error in failures)
    message = (
        f"{rewrite_error}; workspace manifest rollback failed for "
        f"{failed_paths}: {failure_details}"
    )
    failed_path = failures[0][0]
    return ManifestRewriteWriteError(
        message,
        completed_writes=rewrite_error.completed_writes,
        planned_writes=rewrite_error.planned_writes,
        failed_path=failed_path,
    )
