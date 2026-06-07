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
correct staged directory for a single crate. Both detect
index-missing-version failures (:func:`_is_index_missing_version_error`) and
publish-phase already-uploaded errors (:func:`_is_already_published_error`) to
support non-fatal downgrade paths.

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
from lading.commands.publish_errors import PublishError, PublishPreflightError
from lading.commands.publish_execution import (
    _invoke,
)
from lading.commands.publish_index_check import (
    _CargoInvocation,
    _format_cargo_failure_message,
    _is_index_missing_version_error,
)
from lading.commands.publish_index_check import (
    _extract_missing_dependency_name as _extract_missing_dependency_name,
)
from lading.commands.publish_index_check import (
    _handle_index_missing_version as _raw_handle_index_missing_version,
)
from lading.commands.publish_manifest import (
    PublishPreparationError,
    _apply_strip_patch_strategy,
)
from lading.commands.publish_plan import (
    PublishPlan,
    format_plan,
    plan_publication,
)
from lading.commands.publish_plan import (
    PublishPlanError as _PublishPlanError,
)
from lading.commands.publish_preflight import (
    _apply_compiletest_externs,
    _build_preflight_environment,
    _CargoPreflightOptions,
    _compose_preflight_arguments,
    _run_aux_build_commands,
    _run_cargo_preflight,
    _validate_lockfile_freshness,
    _verify_clean_working_tree,
)
from lading.utils.path import normalise_workspace_root
from lading.workspace import metadata as _metadata_module

StripPatchesSetting = config_module.StripPatchesSetting
metadata_module = _metadata_module
PublishPlanError = _PublishPlanError

LOGGER = logging.getLogger(__name__)

if typ.TYPE_CHECKING:
    from lading.config import LadingConfig
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceCrate, WorkspaceGraph


@dc.dataclass(frozen=True, slots=True)
class _PublishExecutionOptions:
    """Runtime flags that affect cargo package/publish invocations."""

    live: bool
    allow_dirty: bool
    allow_unpublished_workspace_deps: bool = False


@dc.dataclass(frozen=True, slots=True)
class _PublicationPipelineState:
    """Shared publish state for cargo package and publish invocations."""

    plan: PublishPlan
    preparation: PublishPreparation
    options: _PublishExecutionOptions


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
    """Stage a workspace copy and propagate workspace READMEs."""
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


def _resolve_staged_crate_root(
    crate: WorkspaceCrate,
    plan: PublishPlan,
    staging_root: Path,
) -> Path:
    """Return the staged crate root, ensuring it resides within the workspace."""
    try:
        relative_root = crate.root_path.relative_to(plan.workspace_root)
    except ValueError as exc:  # pragma: no cover - defensive guard
        message = (
            f"Crate {crate.name!r} root {crate.root_path} is outside workspace "
            f"{plan.workspace_root}"
        )
        raise PublishPreparationError(message) from exc

    staged_root = staging_root / relative_root
    if not staged_root.exists():  # pragma: no cover - defensive guard
        message = f"Staged crate root not found for {crate.name!r}: {staged_root}"
        raise PublishPreparationError(message)

    return staged_root


def _handle_index_missing_version(
    invocation: _CargoInvocation,
    *,
    plan: PublishPlan,
    options: _PublishExecutionOptions,
) -> None:
    """Pick the phase-appropriate error class and delegate to the helper.

    Resolve the error class here based on the cargo subcommand and pass it
    through to the relocated implementation in ``publish_index_check``.
    """
    error_cls = (
        PublishError if invocation.subcommand == "publish" else PublishPreflightError
    )
    _raw_handle_index_missing_version(
        invocation, plan=plan, options=options, error_cls=error_cls
    )


def _package_publishable_crates(
    plan: PublishPlan,
    preparation: PublishPreparation,
    *,
    options: _PublishExecutionOptions,
    runner: CommandRunner,
) -> None:
    """Package each publishable crate in order using the staged workspace."""
    state = _PublicationPipelineState(plan, preparation, options)
    for crate in plan.publishable:
        _package_crate(
            crate,
            state,
            runner=runner,
        )


def _package_crate(
    crate: WorkspaceCrate,
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> None:
    """Package one publishable crate using the staged workspace."""
    plan = state.plan
    options = state.options
    package_args: tuple[str, ...] = ("--allow-dirty",) if options.allow_dirty else ()
    crate_root = _resolve_staged_crate_root(crate, plan, state.preparation.staging_root)
    LOGGER.info("Running cargo package for crate %s", crate.name)
    exit_code, stdout, stderr = runner(
        ("cargo", "package", *package_args),
        cwd=crate_root,
        env=None,
    )
    if exit_code == 0:
        LOGGER.info("Successfully packaged crate %s", crate.name)
        return
    if _is_index_missing_version_error(exit_code, stdout, stderr):
        _handle_index_missing_version(
            _CargoInvocation(
                crate_name=crate.name,
                subcommand="package",
                output=(exit_code, stdout, stderr),
            ),
            plan=plan,
            options=options,
        )
        return
    message = _format_cargo_failure_message(
        "package", crate.name, exit_code, (stdout, stderr)
    )
    LOGGER.error(message)
    raise PublishPreflightError(message)


_ALREADY_PUBLISHED_MARKERS: tuple[str, ...] = (
    "already uploaded",
    "already published",
    "already exists on crates.io",
    "already exists on crates.io index",
)

_CARGO_REGISTRY_ERROR_CODE = 101


def _is_already_published_error(exit_code: int, stdout: str, stderr: str) -> bool:
    """Return True when ``cargo publish`` failed because the version exists.

    Cargo returns exit code 101 for registry errors including already-published
    versions. This function checks both the exit code and output to minimise
    false positives from unrelated failures.
    """
    # Only consider exit code 101 (cargo registry error)
    if exit_code != _CARGO_REGISTRY_ERROR_CODE:
        return False

    haystack = f"{stdout}\n{stderr}".lower()
    return any(marker in haystack for marker in _ALREADY_PUBLISHED_MARKERS)


def _publish_crates(
    plan: PublishPlan,
    preparation: PublishPreparation,
    *,
    runner: CommandRunner,
    options: _PublishExecutionOptions,
) -> None:
    """Publish each crate in order, respecting dry-run vs live mode."""
    state = _PublicationPipelineState(plan, preparation, options)
    for crate in plan.publishable:
        _publish_crate(
            crate,
            state,
            runner=runner,
        )


def _publish_crate(
    crate: WorkspaceCrate,
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> None:
    """Publish one crate from the staged workspace."""
    plan = state.plan
    options = state.options
    publish_args: list[str] = []
    if options.allow_dirty:
        publish_args.append("--allow-dirty")
    if not options.live:
        publish_args.append("--dry-run")
    publish_args_tuple = tuple(publish_args)
    crate_root = _resolve_staged_crate_root(crate, plan, state.preparation.staging_root)
    LOGGER.info(
        "Running cargo publish%s for crate %s",
        "" if options.live else " --dry-run",
        crate.name,
    )
    exit_code, stdout, stderr = runner(
        ("cargo", "publish", *publish_args_tuple),
        cwd=crate_root,
        env=None,
    )
    _handle_publish_result(
        _CargoInvocation(
            crate_name=crate.name,
            subcommand="publish",
            output=(exit_code, stdout, stderr),
        ),
        crate,
        plan,
        options,
    )


def _handle_publish_result(
    invocation: _CargoInvocation,
    crate: WorkspaceCrate,
    plan: PublishPlan,
    options: _PublishExecutionOptions,
) -> None:
    """Handle a completed ``cargo publish`` invocation."""
    exit_code, stdout, stderr = invocation.output
    if exit_code == 0:
        success_message = (
            "Successfully published crate %s"
            if options.live
            else "Dry-run publish succeeded for crate %s"
        )
        LOGGER.info(success_message, invocation.crate_name)
        return
    if _is_already_published_error(exit_code, stdout, stderr):
        LOGGER.warning(
            "Crate %s @ %s is already published; skipping",
            crate.name,
            crate.version,
        )
        return
    if _is_index_missing_version_error(exit_code, stdout, stderr):
        # cargo publish --dry-run packages internally and hits the same
        # crates.io index lookup as cargo package, so honour the override
        # consistently across both phases.
        _handle_index_missing_version(invocation, plan=plan, options=options)
        return

    message = _format_cargo_failure_message(
        "publish", crate.name, exit_code, (stdout, stderr)
    )
    LOGGER.error(message)
    raise PublishError(message)


def _execute_live_publication_pipeline(
    plan: PublishPlan,
    preparation: PublishPreparation,
    *,
    options: _PublishExecutionOptions,
    runner: CommandRunner,
) -> None:
    """Package and publish each crate before moving to the next crate."""
    state = _PublicationPipelineState(plan, preparation, options)
    completed: list[str] = []
    for crate in plan.publishable:
        LOGGER.info("Live pipeline: starting crate %s", crate.name)
        try:
            _package_crate(
                crate,
                state,
                runner=runner,
            )
            _publish_crate(
                crate,
                state,
                runner=runner,
            )
        except PublishPreparationError as exc:
            # Preparation failures escape the preflight/publish error taxonomy;
            # normalise them so the live pipeline reports a single abort class.
            LOGGER.exception(
                "Live pipeline: aborted on crate %s — %d/%d crates completed (%s)",
                crate.name,
                len(completed),
                len(plan.publishable),
                ", ".join(completed) if completed else "none",
            )
            raise PublishPreflightError(str(exc)) from exc
        except (PublishPreflightError, PublishError):
            LOGGER.exception(
                "Live pipeline: aborted on crate %s — %d/%d crates completed (%s)",
                crate.name,
                len(completed),
                len(plan.publishable),
                ", ".join(completed) if completed else "none",
            )
            raise
        LOGGER.info("Live pipeline: completed crate %s", crate.name)
        completed.append(crate.name)


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
            "--allow-unpublished-workspace-deps is only valid in dry-run mode; "
            "re-run without --live."
        )
        LOGGER.error(message)
        raise PublishPreflightError(message)
    if options.allow_unpublished_workspace_deps:
        LOGGER.info(
            "Allowing unpublished workspace dependencies during dry-run publish"
        )


def _dispatch_publication(
    plan: PublishPlan,
    preparation: PublishPreparation,
    *,
    options: _PublishExecutionOptions,
    runner: CommandRunner,
) -> None:
    """Route to the live or dry-run publication pipeline."""
    if options.live:
        LOGGER.info("Publication mode: live (interleaved per-crate pipeline)")
        _execute_live_publication_pipeline(
            plan,
            preparation,
            options=options,
            runner=runner,
        )
    else:
        LOGGER.info("Publication mode: dry-run (batched two-phase pipeline)")
        _package_publishable_crates(
            plan,
            preparation,
            options=options,
            runner=runner,
        )
        LOGGER.info("Dry-run pipeline: packaging complete; starting publish phase")
        _publish_crates(
            plan,
            preparation,
            runner=runner,
            options=options,
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


def _run_preflight_checks(
    workspace_root: Path,
    *,
    allow_dirty: bool,
    configuration: LadingConfig | None = None,
    runner: CommandRunner | None = None,
) -> None:
    """Execute publish pre-flight checks for ``workspace_root``."""
    command_runner = runner or _invoke
    active_configuration = _ensure_configuration(configuration, workspace_root)
    preflight_config = active_configuration.preflight
    base_env = _build_preflight_environment(preflight_config.env_overrides)
    _verify_clean_working_tree(
        workspace_root,
        allow_dirty=allow_dirty,
        runner=command_runner,
        env=base_env,
    )
    _run_aux_build_commands(
        workspace_root,
        preflight_config.aux_build,
        runner=command_runner,
        env=base_env,
    )
    _validate_lockfile_freshness(
        workspace_root,
        runner=command_runner,
        env=base_env,
    )

    with tempfile.TemporaryDirectory(prefix="lading-preflight-target-") as target:
        target_path = Path(target)
        unit_tests_only = preflight_config.unit_tests_only
        check_arguments, test_arguments = _preflight_argument_sets(
            target_path, unit_tests_only=unit_tests_only
        )
        _run_cargo_preflight(
            workspace_root,
            "check",
            runner=command_runner,
            options=_CargoPreflightOptions(
                extra_args=check_arguments,
                env=base_env,
            ),
        )
        test_env = _apply_compiletest_externs(
            base_env,
            preflight_config.compiletest_externs,
            workspace_root=workspace_root,
        )
        _run_cargo_preflight(
            workspace_root,
            "test",
            runner=command_runner,
            options=_CargoPreflightOptions(
                extra_args=test_arguments,
                test_excludes=preflight_config.test_exclude,
                unit_tests_only=unit_tests_only,
                env=test_env,
                diagnostics_tail_lines=preflight_config.stderr_tail_lines,
            ),
        )


def _preflight_argument_sets(
    target_dir: Path, *, unit_tests_only: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return argument tuples for cargo check and cargo test pre-flight calls."""
    check_arguments = _compose_preflight_arguments(target_dir, include_all_targets=True)
    test_arguments = _compose_preflight_arguments(
        target_dir, include_all_targets=not unit_tests_only
    )
    return check_arguments, test_arguments
