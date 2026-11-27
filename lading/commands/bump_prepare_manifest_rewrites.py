"""Prepare configured non-member manifest rewrites for the bump plan.

The planner reads and rewrites selected fixture manifests without mutating
them. This adapter confines each planned destination and proposes its complete
new text to the bump plan, so fixture writes commit with the manifest stage and
before documentation, README and lockfile work.
"""

import collections.abc as cabc
import time
import typing as typ

from lading.commands import (
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrite_metrics,
    bump_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrite_metrics import (
    ManifestRewriteMetricLabels,
)
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteVersions,
)
from lading.commands.bump_plan import PlanBuilder, WriteCategory
from lading.utils.confinement import (
    BumpPreflightError,
    confine_relative_path,
    establish_workspace_root,
)

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from pathlib import Path

    from lading.commands.bump_manifest_rewrites import ManifestRewritePlan
    from lading.commands.bump_manifests import _BumpContext
    from lading.config import ManifestRewriteConfig


def prepare_manifest_rewrites(
    context: _BumpContext,
    target_version: str,
    builder: PlanBuilder,
) -> tuple[ManifestRewritePlan, ...]:
    """Plan configured fixture rewrites and propose their new text.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    target_version : str
        Version to apply.
    builder : PlanBuilder
        Proposal sink.

    Returns
    -------
    tuple[ManifestRewritePlan, ...]
        The planned rewrites, empty when none are configured.

    Raises
    ------
    ManifestRewriteError
        If a configured manifest is unsafe, unreadable, or cannot be planned.
    """
    rewrite_groups = context.configuration.bump.manifest_rewrites
    if not rewrite_groups:
        return ()
    started_at = time.perf_counter()
    dry_run = context.base_options.dry_run
    try:
        plans = _plan_rewrites(context, target_version, rewrite_groups)
        _propose_rewrites(context, builder, plans)
    except ManifestRewriteError:
        _record_metrics(
            time.perf_counter() - started_at,
            outcome="failed",
            dry_run=dry_run,
            error_category="planning",
        )
        raise
    _record_metrics(
        time.perf_counter() - started_at,
        outcome="planned",
        dry_run=dry_run,
        error_category="none",
    )
    return plans


def _plan_rewrites(
    context: _BumpContext,
    target_version: str,
    rewrite_groups: cabc.Sequence[ManifestRewriteConfig],
) -> tuple[ManifestRewritePlan, ...]:
    """Read selected sources and plan their rewritten text."""
    sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
        context.root_path,
        rewrite_groups,
        tuple(crate.manifest_path for crate in context.workspace.crates),
    )
    return bump_manifest_rewrites.plan_manifest_rewrites(
        sources,
        ManifestRewriteVersions(
            updated_crate_names=context.updated_crate_names,
            pre_bump_versions={
                crate.name: crate.version for crate in context.workspace.crates
            },
            target_version=target_version,
        ),
    )


def _propose_rewrites(
    context: _BumpContext,
    builder: PlanBuilder,
    plans: cabc.Sequence[ManifestRewritePlan],
) -> None:
    """Confine each planned destination and propose its rewritten text."""
    root = establish_workspace_root(context.root_path)
    for plan in plans:
        relative = _relative_rewrite_path(plan, root)
        key = f"manifest rewrite {relative.as_posix()}"
        try:
            destination = confine_relative_path(root, relative.as_posix(), key=key)
        except BumpPreflightError as exc:
            message = f"{key}: cannot write the planned rewrite: {exc}"
            raise ManifestRewriteError(message) from exc
        builder.propose(destination, plan.new_text, WriteCategory.FIXTURE_MANIFEST)


def _relative_rewrite_path(plan: ManifestRewritePlan, root: Path) -> Path:
    """Return the planned destination relative to the canonical workspace root."""
    try:
        return plan.path.relative_to(root)
    except ValueError as exc:
        message = f"Manifest rewrite plan escaped the workspace root: {plan.path}"
        raise ManifestRewriteError(message) from exc


def _record_metrics(
    duration_seconds: float,
    *,
    outcome: str,
    dry_run: bool,
    error_category: str,
) -> None:
    """Record one planning outcome and its duration."""
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        duration_seconds,
        ManifestRewriteMetricLabels(
            phase="planning",
            outcome=outcome,
            dry_run=dry_run,
            error_category=error_category,
        ),
    )


def record_application_metrics(
    context: _BumpContext,
    duration_seconds: float,
    *,
    failed: bool,
) -> None:
    """Record the commit-phase outcome for configured rewrites.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    duration_seconds : float
        Time spent committing the prepared plan.
    failed : bool
        Whether the commit failed.
    """
    if not context.configuration.bump.manifest_rewrites:
        return
    dry_run = context.base_options.dry_run
    if failed:
        outcome, error_category = "failed", "write"
    elif dry_run:
        outcome, error_category = "skipped", "none"
    else:
        outcome, error_category = "applied", "none"
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        duration_seconds,
        ManifestRewriteMetricLabels(
            phase="application",
            outcome=outcome,
            dry_run=dry_run,
            error_category=error_category,
        ),
    )
