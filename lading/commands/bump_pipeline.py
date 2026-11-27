"""Run the ordered stages of a version bump.

The pipeline is split into a preparation phase and a mutation phase. All
manifest, documentation, README, and lockfile destinations are validated and
their complete contents computed by :func:`_prepare_plan`; only then does
:func:`lading.commands.bump_plan.commit_plan` write anything. Lockfile
regeneration consumes the validated plan instead of rediscovering manifests.
"""

import logging
import time
import typing as typ

from lading.commands import (
    bump_lockfiles,
    bump_prepare_manifest_rewrites,
    bump_workspace_write_lock,
)
from lading.commands.bump_plan import (
    BumpPlan,
    PlanBuilder,
    WriteCategory,
    changes_from_plan,
    commit_plan,
)
from lading.commands.bump_prepare import (
    prepare_documentation,
    prepare_manifests,
    prepare_readmes,
)

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from pathlib import Path

    from lading.commands.bump_lockfile_planning import LockfilePlan
    from lading.commands.bump_manifests import _BumpContext
    from lading.commands.bump_output import BumpChanges

_log = logging.getLogger(__name__)


def _run_pipeline(context: _BumpContext, target_version: str) -> BumpChanges:
    """Prepare, commit, and regenerate, returning the ordered changes."""
    if context.base_options.dry_run:
        plan = _prepare_plan(context, target_version)
        bump_prepare_manifest_rewrites.record_application_metrics(
            context, 0.0, failed=False
        )
        return changes_from_plan(plan, lockfiles=_planned_lockfiles(plan))
    with bump_workspace_write_lock.workspace_write_lock(context.root_path):
        return _commit_prepared_run(context, target_version)


def _commit_prepared_run(
    context: _BumpContext,
    target_version: str,
) -> BumpChanges:
    """Prepare and commit one live run while the workspace lock is held."""
    plan = _prepare_plan(context, target_version)
    started_at = time.perf_counter()
    try:
        commit_plan(plan)
    except BaseException:
        bump_prepare_manifest_rewrites.record_application_metrics(
            context, time.perf_counter() - started_at, failed=True
        )
        raise
    bump_prepare_manifest_rewrites.record_application_metrics(
        context, time.perf_counter() - started_at, failed=False
    )
    return changes_from_plan(plan, lockfiles=_regenerate_plan(context, plan))


def _prepare_plan(context: _BumpContext, target_version: str) -> BumpPlan:
    """Validate every target and return the complete write plan."""
    builder = PlanBuilder(root=context.root_path)
    prepare_manifests(context, target_version, builder)
    bump_prepare_manifest_rewrites.prepare_manifest_rewrites(
        context, target_version, builder
    )
    prepare_documentation(context, target_version, builder)
    prepare_readmes(context, builder)
    lockfiles = _plan_lockfiles(context, builder)
    return builder.build(target_version=target_version, lockfiles=lockfiles)


def _plan_lockfiles(
    context: _BumpContext,
    builder: PlanBuilder,
) -> LockfilePlan | None:
    """Validate lockfile destinations when regeneration is enabled.

    Planning is read-only, so it runs whenever regeneration is enabled: an
    invalid configured manifest must be rejected before any write, even when
    no manifest changes and regeneration will therefore be skipped. The plan
    reaches the commit phase only when a manifest changed.

    Returns
    -------
    LockfilePlan | None
        The validated plan, or ``None`` when regeneration is disabled or no
        manifest changes.
    """
    if context.base_options.rebuild_lockfiles is not True:
        return None
    repository = _lockfile_repository(context)
    plan = repository.plan_lockfiles(
        context.root_path, context.configuration.bump.lockfile_manifests
    )
    if not _plan_changes_manifests(builder):
        return None
    return plan


def _plan_changes_manifests(builder: PlanBuilder) -> bool:
    """Return whether the plan changes a workspace or fixture manifest."""
    return builder.has_changes(WriteCategory.MANIFEST) or builder.has_changes(
        WriteCategory.FIXTURE_MANIFEST
    )


def _planned_lockfiles(plan: BumpPlan) -> tuple[Path, ...]:
    """Return the lockfile destinations a dry run would regenerate."""
    return () if plan.lockfiles is None else plan.lockfiles.lockfiles


def _regenerate_plan(
    context: _BumpContext,
    plan: BumpPlan,
) -> tuple[Path, ...]:
    """Regenerate the lockfiles named by the committed plan."""
    if plan.lockfiles is None:
        return ()
    repository = _lockfile_repository(context)
    return repository.regenerate_planned(plan.lockfiles)


def _lockfile_repository(context: _BumpContext) -> bump_lockfiles.LockfileRepository:
    """Return the configured lockfile repository or the cargo adapter."""
    configured = context.base_options.lockfile_repository
    if configured is not None:
        return configured
    return bump_lockfiles.CargoLockfileRepository()
