"""Run the ordered stages of a version bump.

The pipeline is split into a preparation phase and a mutation phase. All
manifest, documentation, README, and lockfile destinations are validated and
their complete contents computed by :func:`_prepare_plan`; only then does
:func:`lading.commands.bump_plan.commit_plan` write anything. Lockfile
regeneration consumes the validated plan instead of rediscovering manifests.
"""

import logging
import typing as typ

from lading.commands import bump_lockfiles
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
    plan = _prepare_plan(context, target_version)
    if context.base_options.dry_run:
        return changes_from_plan(plan, lockfiles=_planned_lockfiles(plan))
    commit_plan(plan)
    return changes_from_plan(plan, lockfiles=_regenerate_plan(context, plan))


def _prepare_plan(context: _BumpContext, target_version: str) -> BumpPlan:
    """Validate every target and return the complete write plan."""
    builder = PlanBuilder(root=context.root_path)
    prepare_manifests(context, target_version, builder)
    prepare_documentation(context, target_version, builder)
    prepare_readmes(context, builder)
    lockfiles = _plan_lockfiles(context, builder)
    return builder.build(target_version=target_version, lockfiles=lockfiles)


def _plan_lockfiles(
    context: _BumpContext,
    builder: PlanBuilder,
) -> LockfilePlan | None:
    """Plan lockfiles when regeneration is enabled and a manifest changes."""
    if context.base_options.rebuild_lockfiles is not True:
        return None
    if not builder.has_changes(WriteCategory.MANIFEST):
        return None
    repository = _lockfile_repository(context)
    return repository.plan_lockfiles(
        context.root_path, context.configuration.bump.lockfile_manifests
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
    return (
        context.base_options.lockfile_repository
        or bump_lockfiles.CargoLockfileRepository()
    )
