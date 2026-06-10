"""Orchestrate the ``lading publish`` workflow.

``run()`` is the primary entry point. It loads configuration, discovers the
workspace graph, runs pre-flight checks (:mod:`~lading.commands.publish_preflight`),
plans publication order, stages the workspace, and dispatches to one of two
**publish pipelines** depending on the ``live`` flag.

**Pipeline dispatch**

*Dry-run* (``live=False``) keeps the historical batched two-phase pipeline:
package every publishable crate, then ``cargo publish --dry-run`` every crate
in plan order.

*Live* (``live=True``) interleaves packaging and publishing per crate via
:func:`_execute_live_publication_pipeline`: package the next crate, publish it,
then advance. This ordering lets dependent crates resolve newly uploaded
in-plan dependencies during a single release train.

**Per-crate helpers**

:func:`_package_crate` and :func:`_publish_crate` each invoke ``cargo`` in the
correct staged directory for a single crate. Both adapt cargo
index-missing-version output into structured failures and detect publish-phase
already-uploaded errors (:func:`_is_already_published_error`) to support
non-fatal downgrade paths.

**Error boundary**

:class:`~lading.commands.publish_errors.PublishPreflightError` signals
pre-publication failures (packaging, pre-flight checks).
:class:`~lading.commands.publish_errors.PublishError` signals post-pre-flight
publish failures and subclasses the preflight error so callers can handle all
failures through one catch boundary or distinguish the publish phase when
needed.

**Related modules**

* :mod:`lading.commands.publish_plan` — plan construction and formatting
* :mod:`lading.commands.publish_preflight` — ``cargo check`` / ``cargo test`` /
  git-status guards
* :mod:`lading.commands.publish_errors` — :class:`PublishPreflightError` and
  :class:`PublishError`
* :mod:`lading.commands.publish_execution` — subprocess invocation and cmd-mox
  integration
"""

from __future__ import annotations

import atexit
import dataclasses as dc
import logging
import shutil
import tempfile
import typing as typ
from pathlib import Path

from lading import config as config_module
from lading.commands import publish_pipeline as _pipeline_module
from lading.commands import publish_preflight as _publish_preflight
from lading.commands.publish_errors import PublishPreflightError
from lading.commands.publish_execution import (
    _invoke,
)
from lading.commands.publish_manifest import (
    PublishPreparationError,
    _apply_strip_patch_strategy,
)
from lading.commands.publish_plan import (
    PublishPlan,
    append_section,
    format_plan,
    plan_publication,
)
from lading.commands.publish_plan import (
    PublishPlanError as _PublishPlanError,
)
from lading.utils.path import normalise_workspace_root
from lading.workspace import metadata as _metadata_module

StripPatchesSetting = config_module.StripPatchesSetting
metadata_module = _metadata_module
PublishPlanError = _PublishPlanError
_append_section = append_section
_format_plan = format_plan
_CargoPreflightOptions = _publish_preflight._CargoPreflightOptions
_apply_compiletest_externs = _publish_preflight._apply_compiletest_externs
_build_preflight_environment = _publish_preflight._build_preflight_environment
_build_test_arguments = _publish_preflight._build_test_arguments
_compose_preflight_arguments = _publish_preflight._compose_preflight_arguments
_normalise_test_excludes = _publish_preflight._normalise_test_excludes
_run_aux_build_commands = _publish_preflight._run_aux_build_commands
_run_cargo_preflight = _publish_preflight._run_cargo_preflight
_validate_lockfile_freshness = _publish_preflight._validate_lockfile_freshness
_verify_clean_working_tree = _publish_preflight._verify_clean_working_tree

# Canonical preflight entry points live in publish_preflight (issue #96);
# these aliases keep the historical publish.* access used by tests resolving.
_run_preflight_checks = _publish_preflight._run_preflight_checks
_preflight_argument_sets = _publish_preflight._preflight_argument_sets

PublishError = _pipeline_module.PublishError

# Backwards-compatible aliases (issue #108): publish_pipeline owns the
# per-crate publication pipeline; existing tests reach these helpers
# through this module.
_PublishExecutionOptions = _pipeline_module._PublishExecutionOptions
_PublicationPipelineState = _pipeline_module._PublicationPipelineState
_dispatch_publication = _pipeline_module._dispatch_publication
_execute_live_publication_pipeline = _pipeline_module._execute_live_publication_pipeline
_format_cargo_failure_message = _pipeline_module._format_cargo_failure_message
_handle_index_missing_version = _pipeline_module._handle_index_missing_version
_handle_publish_result = _pipeline_module._handle_publish_result
_is_already_published_error = _pipeline_module._is_already_published_error
_package_crate = _pipeline_module._package_crate
_package_publishable_crates = _pipeline_module._package_publishable_crates
_publish_crate = _pipeline_module._publish_crate
_publish_crates = _pipeline_module._publish_crates
_resolve_staged_crate_root = _pipeline_module._resolve_staged_crate_root

LOGGER = logging.getLogger(__name__)

if typ.TYPE_CHECKING:
    from lading.config import LadingConfig
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceGraph


@dc.dataclass(frozen=True, slots=True)
class PublishOptions:
    """Runtime configuration for publish planning, staging, and checks.

    Parameters
    ----------
    allow_dirty:
        When ``True`` the git cleanliness guard is skipped.
    live:
        When :data:`True`, execute ``cargo publish`` without ``--dry-run``.
        Defaults to :data:`False` so publishing remains a dry-run unless
        explicitly enabled.
    build_directory:
        Optional directory used to stage workspace artifacts. When ``None``,
        a temporary directory is created for each invocation.
    preserve_symlinks:
        Control whether staging preserves symbolic links in the workspace
        clone instead of dereferencing them.
    cleanup:
        When :data:`True`, the staged workspace is removed automatically on
        process exit.
    configuration:
        Optional :class:`~lading.config.LadingConfig` instance to reuse instead
        of loading from disk.
    workspace:
        Optional pre-loaded workspace graph to reuse for planning.
    command_runner:
        Optional callable used to execute shell commands. Primarily intended
        for tests and dependency injection.
    allow_unpublished_workspace_deps:
        When :data:`True`, downgrade ``cargo package`` failures caused by a
        sibling workspace crate version not yet visible on the crates.io index
        to a warning, provided the missing crate is part of the planned
        publish set. Only valid in dry-run mode (``live=False``); combining it
        with ``live=True`` raises :class:`PublishPreflightError`.

    """

    allow_dirty: bool = True
    live: bool = False
    build_directory: Path | None = None
    preserve_symlinks: bool = True
    cleanup: bool = False
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    command_runner: CommandRunner | None = None
    allow_unpublished_workspace_deps: bool = False


@dc.dataclass(frozen=True, slots=True)
class PublishPreparation:
    """Details about the staged workspace copy."""

    staging_root: Path
    copied_readmes: tuple[Path, ...]


def _normalise_build_directory(
    workspace_root: Path, build_directory: Path | None
) -> Path:
    """Return a directory suitable for staging workspace artifacts."""
    if build_directory is None:
        return Path(tempfile.mkdtemp(prefix="lading-publish-"))

    candidate = Path(build_directory).expanduser()
    candidate = candidate.resolve(strict=False)

    workspace_root = workspace_root.resolve(strict=True)
    if candidate.is_relative_to(workspace_root):
        message = "Publish build directory cannot reside within the workspace root"
        raise PublishPreparationError(message)

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _copy_workspace_tree(
    workspace_root: Path, build_directory: Path, *, preserve_symlinks: bool
) -> Path:
    """Copy ``workspace_root`` into ``build_directory`` and return the clone.

    When ``preserve_symlinks`` is :data:`True`, the cloned tree keeps symbolic
    links instead of dereferencing them. This avoids unexpectedly copying large
    directories outside the workspace while still allowing callers to opt into
    dereferencing if required.
    """
    workspace_root = workspace_root.resolve(strict=True)
    staging_root = build_directory / workspace_root.name
    if staging_root.resolve(strict=False).is_relative_to(workspace_root):
        message = "Publish staging directory cannot be nested inside the workspace root"
        raise PublishPreparationError(message)
    if staging_root.exists():
        shutil.rmtree(staging_root)
    shutil.copytree(workspace_root, staging_root, symlinks=preserve_symlinks)
    return staging_root


def prepare_workspace(
    plan: PublishPlan,
    workspace: WorkspaceGraph,
    *,
    options: PublishOptions | None = None,
) -> PublishPreparation:
    """Stage a workspace copy for publishing."""
    active_options = PublishOptions() if options is None else options
    build_directory = _normalise_build_directory(
        plan.workspace_root, active_options.build_directory
    )
    LOGGER.info(
        "Preparing staged workspace for publication under %s",
        build_directory,
    )
    staging_root = _copy_workspace_tree(
        plan.workspace_root,
        build_directory,
        preserve_symlinks=active_options.preserve_symlinks,
    )
    LOGGER.info("Staged workspace created at %s", staging_root)
    LOGGER.info("Workspace README staging skipped; handled by lading bump")
    preparation = PublishPreparation(staging_root=staging_root, copied_readmes=())
    if active_options.cleanup:
        build_root = staging_root.parent

        def _cleanup() -> None:
            """Remove the staged build directory on process exit."""
            shutil.rmtree(build_root, ignore_errors=True)

        atexit.register(_cleanup)
    return preparation


def _format_preparation_summary(preparation: PublishPreparation) -> tuple[str, ...]:
    """Return formatted summary lines for staging results."""
    lines = [f"Staged workspace at: {preparation.staging_root}"]
    lines.append("Workspace READMEs are handled by lading bump.")
    return tuple(lines)


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
    """Raise :class:`PublishPreflightError` for invalid option combinations."""
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
    """Run pre-flight checks, package crates, and publish from ``workspace_root``."""
    root_path = normalise_workspace_root(workspace_root)
    LOGGER.info("Starting publish workflow for workspace %s", root_path)
    effective_options = PublishOptions() if options is None else options
    _validate_publication_options(effective_options)
    configuration_override = configuration or effective_options.configuration
    workspace_override = workspace or effective_options.workspace
    command_runner = effective_options.command_runner or _invoke
    active_configuration = _ensure_configuration(configuration_override, root_path)
    active_workspace = _ensure_workspace(workspace_override, root_path)

    _run_preflight_checks(
        root_path,
        allow_dirty=effective_options.allow_dirty,
        configuration=active_configuration,
        runner=command_runner,
    )
    plan = plan_publication(
        active_workspace, active_configuration, workspace_root=root_path
    )
    preparation = prepare_workspace(plan, active_workspace, options=options)
    _apply_strip_patch_strategy(
        preparation.staging_root,
        plan,
        active_configuration.publish.strip_patches,
    )
    execution_options = _PublishExecutionOptions(
        live=effective_options.live,
        allow_dirty=effective_options.allow_dirty,
        allow_unpublished_workspace_deps=(
            effective_options.allow_unpublished_workspace_deps
        ),
    )
    _dispatch_publication(
        plan,
        preparation,
        options=execution_options,
        runner=command_runner,
    )
    plan_message = format_plan(
        plan, strip_patches=active_configuration.publish.strip_patches
    )
    summary_lines = _format_preparation_summary(preparation)
    LOGGER.info("Publish workflow completed successfully for workspace %s", root_path)
    return f"{plan_message}\n\n" + "\n".join(summary_lines)
