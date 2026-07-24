"""Coordinate planning, preflight checks, staging, and publication."""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

from lading import config as config_module
from lading.commands import publish_pipeline, publish_preflight, publish_staging
from lading.commands.publish_errors import PublishPreflightError
from lading.commands.publish_manifest import _apply_strip_patch_strategy
from lading.commands.publish_plan import PublishPlanError as PublishPlanError
from lading.commands.publish_plan import format_plan, plan_publication
from lading.utils.path import normalise_workspace_root

if typ.TYPE_CHECKING:
    from lading.config import LadingConfig
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceGraph

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class PublishOptions:
    """Runtime configuration for publish planning, staging, and checks."""

    allow_dirty: bool = True
    live: bool = False
    build_directory: Path | None = None
    preserve_symlinks: bool = True
    cleanup: bool = False
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    command_runner: CommandRunner | None = None
    allow_unpublished_workspace_deps: bool = False


def _ensure_configuration(
    configuration: LadingConfig | None, workspace_root: Path
) -> LadingConfig:
    """Return the active configuration, loading it from disk when required."""
    if configuration is not None:
        return configuration
    try:
        return config_module.current_configuration()
    except config_module.ConfigurationNotLoadedError:
        return config_module.load_configuration(workspace_root)


def _ensure_workspace(
    workspace: WorkspaceGraph | None, workspace_root: Path
) -> WorkspaceGraph:
    """Return the workspace graph rooted at ``workspace_root``."""
    if workspace is not None:
        return workspace
    from lading.workspace import WorkspaceModelError, load_workspace

    try:
        return load_workspace(workspace_root)
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        message = f"Workspace root not found: {workspace_root}"
        raise WorkspaceModelError(message) from exc


def _validate_publication_options(options: PublishOptions) -> None:
    """Raise for invalid publish option combinations."""
    if options.live and options.allow_unpublished_workspace_deps:
        message = (
            "Unpublished workspace dependency override is only valid in dry-run "
            "mode. Live publish requires all dependency packages to be "
            "available on crates.io before the dependent crate is published."
        )
        LOGGER.error(message)
        raise PublishPreflightError(message)
    if options.allow_unpublished_workspace_deps:
        LOGGER.info(
            "Allowing unpublished workspace dependencies during dry-run publish"
        )


def run(
    workspace_root: Path,
    configuration: LadingConfig | None = None,
    workspace: WorkspaceGraph | None = None,
    *,
    options: PublishOptions | None = None,
) -> str:
    """Run preflight checks, package crates, and publish from ``workspace_root``."""
    root_path = normalise_workspace_root(workspace_root)
    LOGGER.info("Starting publish workflow for workspace %s", root_path)
    effective_options = PublishOptions() if options is None else options
    _validate_publication_options(effective_options)
    active_configuration = _ensure_configuration(
        configuration or effective_options.configuration, root_path
    )
    active_workspace = _ensure_workspace(
        workspace or effective_options.workspace, root_path
    )
    command_runner = effective_options.command_runner or publish_pipeline._invoke
    publish_preflight._run_preflight_checks(
        root_path,
        allow_dirty=effective_options.allow_dirty,
        configuration=active_configuration,
        runner=command_runner,
    )
    plan = plan_publication(
        active_workspace, active_configuration, workspace_root=root_path
    )
    preparation = publish_staging.prepare_workspace(plan, options=effective_options)
    _apply_strip_patch_strategy(
        preparation.staging_root, plan, active_configuration.publish.strip_patches
    )
    execution_options = publish_pipeline._PublishExecutionOptions(
        live=effective_options.live,
        allow_dirty=effective_options.allow_dirty,
        allow_unpublished_workspace_deps=effective_options.allow_unpublished_workspace_deps,
    )
    publish_pipeline._dispatch_publication(
        plan, preparation, options=execution_options, runner=command_runner
    )
    plan_message = format_plan(
        plan, strip_patches=active_configuration.publish.strip_patches
    )
    summary_lines = publish_staging._format_preparation_summary(preparation)
    LOGGER.info("Publish workflow completed successfully for workspace %s", root_path)
    return f"{plan_message}\n\n" + "\n".join(summary_lines)
