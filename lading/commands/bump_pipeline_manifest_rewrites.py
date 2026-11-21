"""Coordinate configured-manifest rewrites around the bump write stages.

:mod:`lading.commands.bump_pipeline` calls
:func:`_plan_non_member_manifest_rewrites` before any stage writes and
:func:`_apply_non_member_manifest_rewrites` after member-manifest updates. The
functions delegate TOML planning and atomic writes to
:mod:`lading.commands.bump_manifest_rewrites` and record bounded outcomes via
:mod:`lading.commands.bump_manifest_rewrite_metrics`. Durations include only
the planning and application intervals, not the stages between them.
"""

import collections.abc as cabc
import logging
import typing as typ
from pathlib import Path

from lading.commands import bump_manifest_rewrites
from lading.commands.bump_manifest_rewrite_metrics import (
    ManifestRewriteMetricLabels,
    record_manifest_rewrite_metrics,
    record_manifest_rewrite_planned,
)

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifests import _BumpContext

_log = logging.getLogger(__name__)


def _record_rewrite_failure(
    duration_seconds: float | None,
    *,
    phase: typ.Literal["planning", "application"],
    error_category: typ.Literal["planning", "write"],
    dry_run: bool,
) -> None:
    """Record a rewrite failure using the measured rewrite intervals."""
    record_manifest_rewrite_metrics(
        duration_seconds,
        ManifestRewriteMetricLabels(
            phase=phase,
            outcome="failed",
            dry_run=dry_run,
            error_category=error_category,
        ),
    )


def _rewrite_duration_seconds(
    application_timing: tuple[float, float] | None,
    clock: cabc.Callable[[], float],
) -> float | None:
    """Sum planning and application time, excluding intervening stages."""
    match application_timing:
        case None:
            return None
        case (planning_duration, application_started_at):
            return planning_duration + clock() - application_started_at


def _plan_non_member_manifest_rewrites(
    context: _BumpContext,
    target_version: str,
) -> tuple[tuple[bump_manifest_rewrites.ManifestRewritePlan, ...], float | None]:
    """Plan configured fixture changes before any pipeline stage writes."""
    rewrite_groups = context.configuration.bump.manifest_rewrites
    if not rewrite_groups:
        return (), None
    clock = context.base_options.clock
    planning_started_at = clock()
    pre_bump_versions = {
        crate.name: crate.version for crate in context.workspace.crates
    }
    member_manifest_paths = tuple(
        crate.manifest_path for crate in context.workspace.crates
    )
    try:
        plans = bump_manifest_rewrites.plan_manifest_rewrites(
            context.root_path,
            rewrite_groups,
            member_manifest_paths,
            bump_manifest_rewrites.ManifestRewriteVersions(
                updated_crate_names=context.updated_crate_names,
                pre_bump_versions=pre_bump_versions,
                target_version=target_version,
            ),
        )
    except bump_manifest_rewrites.ManifestRewriteError:
        _record_rewrite_failure(
            clock() - planning_started_at,
            phase="planning",
            error_category="planning",
            dry_run=context.base_options.dry_run,
        )
        raise
    planning_duration = clock() - planning_started_at
    record_manifest_rewrite_planned(dry_run=context.base_options.dry_run)
    return plans, planning_duration


def _apply_non_member_manifest_rewrites(
    context: _BumpContext,
    plans: cabc.Sequence[bump_manifest_rewrites.ManifestRewritePlan],
    changed_manifests: set[Path],
    *,
    planning_duration: float | None,
) -> None:
    """Apply planned fixture rewrites and include their resolved paths."""
    dry_run = context.base_options.dry_run
    clock = context.base_options.clock
    _log.debug(
        "Non-member manifest rewrites: %d planned manifest(s), dry_run=%s",
        len(plans),
        dry_run,
    )
    application_timing = (
        (planning_duration, clock()) if planning_duration is not None else None
    )
    try:
        rewritten_paths = bump_manifest_rewrites.apply_manifest_rewrites(
            plans, dry_run=dry_run
        )
    except bump_manifest_rewrites.ManifestRewriteError:
        _record_rewrite_failure(
            _rewrite_duration_seconds(application_timing, clock),
            phase="application",
            error_category="write",
            dry_run=dry_run,
        )
        raise
    duration_seconds = _rewrite_duration_seconds(application_timing, clock)
    changed_manifests.update(path.resolve() for path in rewritten_paths)
    outcome = "skipped" if dry_run or not plans else "applied"
    record_manifest_rewrite_metrics(
        duration_seconds,
        ManifestRewriteMetricLabels(
            phase="application",
            outcome=outcome,
            dry_run=dry_run,
            error_category="none",
        ),
    )
