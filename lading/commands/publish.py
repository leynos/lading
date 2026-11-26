"""Coordinate the ``lading publish`` workflow.

``run()`` is the command entry point used by the CLI. It loads configuration
and the workspace graph, performs preflight checks, creates a publication plan,
stages that plan, and dispatches package and publish operations. The coordinator
delegates workspace copies to :mod:`lading.commands.publish_staging` and
per-crate execution to :mod:`lading.commands.publish_pipeline`; the latter uses
:mod:`lading.commands.publish_execution` for its default subprocess runner.
"""

import dataclasses as dc
import logging
import typing as typ
from contextlib import nullcontext
from functools import partial
from pathlib import Path

from lading import config as config_module
from lading.commands import (
    publish_digest_run,
    publish_pipeline,
    publish_preflight,
    publish_registry,
    publish_staging,
)
from lading.commands.publish_errors import ExpectedDigestsError, PublishPreflightError
from lading.commands.publish_manifest import _apply_strip_patch_strategy
from lading.commands.publish_plan import PublishPlan, format_plan, plan_publication
from lading.commands.publish_plan import PublishPlanError as PublishPlanError
from lading.utils.path import normalize_workspace_root
from lading.workspace import metadata as metadata_module

if typ.TYPE_CHECKING:
    from lading.commands.publish_archive_check import ArchiveVerifier
    from lading.commands.publish_cargo_config import CargoEnvironment
    from lading.commands.publish_skip import SkipPreflightDecision
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceGraph

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class PublishOptions:
    """Runtime configuration for publish planning, staging, and checks.

    Attributes
    ----------
    allow_dirty : bool
        Whether preflight checks permit a workspace with uncommitted changes.
    live : bool
        Whether to invoke ``cargo publish`` without ``--dry-run``.
    build_directory : Path | None
        Optional parent directory for the staged workspace.
    preserve_symlinks : bool
        Whether staging copies symbolic links as links rather than targets.
    cleanup : bool
        Whether the staged workspace is removed when publication ends.
        Defaults to :data:`True`; a staged copy is the whole workspace plus
        its verify build, and leaving it behind filled a 1.9 TB volume
        (issue #269). Set it to :data:`False`, or pass ``--keep-staging``, to
        retain the copy for debugging; the path is then logged.
    configuration : LadingConfig | None
        Optional loaded configuration used instead of loading it from disk.
    workspace : WorkspaceGraph | None
        Optional discovered workspace graph used instead of rediscovery.
    command_runner : CommandRunner | None
        Optional command runner, primarily for dependency injection and tests.
    allow_unpublished_workspace_deps : bool
        Whether dry-run index failures for planned workspace dependencies become
        warnings rather than preflight failures.
    sccache_stats : bool
        Whether to collect compiler-cache statistics for cargo invocations.
    sccache_stats_json : Path | None
        Optional JSON report destination for compiler-cache statistics.
    skip_preflight : SkipPreflightDecision | None
        Optional explicit decision, with its provenance, about skipping the
        preflight auxiliary builds and the cargo check/test pair. When
        ``None``, the ``[preflight] skip`` setting decides.
    expect_digests : Path | None
        Optional ``sha256sum``-format file of expected archive digests
        (``--expect-digests``). A relative path resolves against the workspace
        root. When set, every archive is checked against it before upload,
        ``--locked`` is passed to cargo, and the run is limited to crates.io.
    cargo_environment : CargoEnvironment | None
        The Cargo environment resolved by the caller: ``CARGO_REGISTRY_DEFAULT``,
        Cargo's home directory, and the registry token values to hide from
        subprocess output. Required with ``expect_digests``, which refuses to
        run when it is missing rather than guess from less information;
        ``publish.run`` never reads the ambient environment itself.
    published_digest_fetcher : PublishedDigestFetcher | None
        Optional replacement for the crates.io archive download, so tests
        never reach the network. Only used with ``expect_digests``.
    """

    allow_dirty: bool = True
    live: bool = False
    build_directory: Path | None = None
    preserve_symlinks: bool = True
    cleanup: bool = True
    configuration: config_module.LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    command_runner: CommandRunner | None = None
    allow_unpublished_workspace_deps: bool = False
    sccache_stats: bool = False
    sccache_stats_json: Path | None = None
    skip_preflight: SkipPreflightDecision | None = None
    expect_digests: Path | None = None
    cargo_environment: CargoEnvironment | None = None
    published_digest_fetcher: publish_registry.PublishedDigestFetcher | None = None


def _ensure_configuration(
    configuration: config_module.LadingConfig | None, workspace_root: Path
) -> config_module.LadingConfig:
    """Return the active configuration, loading it from disk when required."""
    if configuration is not None:
        return configuration
    try:
        return config_module.current_configuration()
    except config_module.ConfigurationNotLoadedError:
        return config_module.load_configuration(workspace_root)


def _ensure_workspace(
    workspace: WorkspaceGraph | None, workspace_root: Path, *, locked: bool = False
) -> WorkspaceGraph:
    """Return the workspace graph rooted at ``workspace_root``.

    ``locked`` makes discovery fail on a stale lockfile rather than update it.

    Returns
    -------
    WorkspaceGraph
        ``workspace`` when given, otherwise the discovered graph.
    """
    if workspace is not None:
        return workspace
    from lading.workspace import WorkspaceModelError, load_workspace

    discover = partial(load_workspace, locked=True) if locked else load_workspace
    try:
        return discover(workspace_root)
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
    if options.expect_digests is not None and options.cargo_environment is None:
        message = (
            "--expect-digests needs the Cargo environment resolved by the "
            "caller: pass PublishOptions.cargo_environment (the command line "
            "does this). Lading does not read the ambient environment here, and "
            "guessing could miss an alternate default registry."
        )
        raise ExpectedDigestsError(message)
    if options.allow_unpublished_workspace_deps:
        LOGGER.info(
            "Allowing unpublished workspace dependencies during dry-run publish"
        )


def _preflight_request(
    options: PublishOptions,
    configuration: config_module.LadingConfig,
    runner: CommandRunner,
) -> publish_preflight.PreflightRequest:
    """Bundle the pre-flight inputs this publish run was configured with."""
    return publish_preflight.PreflightRequest(
        allow_dirty=options.allow_dirty,
        configuration=configuration,
        runner=runner,
        skip=options.skip_preflight,
        secrets=()
        if options.cargo_environment is None
        else options.cargo_environment.secrets,
    )


def _execution_options(
    options: PublishOptions, verifier: ArchiveVerifier | None
) -> publish_pipeline._PublishExecutionOptions:
    """Translate the public options into the pipeline's execution flags."""
    return publish_pipeline._PublishExecutionOptions(
        live=options.live,
        allow_dirty=options.allow_dirty,
        allow_unpublished_workspace_deps=options.allow_unpublished_workspace_deps,
        sccache_stats=options.sccache_stats,
        sccache_stats_json=options.sccache_stats_json,
        verifier=verifier,
    )


def _render_summary(
    plan_message: str,
    staged: publish_staging.StagedWorkspace,
    verifier: ArchiveVerifier | None,
) -> str:
    """Join the plan, staging fate, and archive checks into the run summary.

    Called after the staging block ends, not inside it: the removal happens as
    the block exits and can fail, so asking about it from within would report
    a tree as gone whenever `shutil.rmtree` raised.

    Returns
    -------
    str
        The plan, then the staging summary, then any archive-check lines.
    """
    lines = publish_staging._format_preparation_summary(
        staged.preparation, retained=staged.retained
    )
    if verifier is not None:
        lines = (*lines, "", *verifier.summary_lines())
    return f"{plan_message}\n\n" + "\n".join(lines)


@dc.dataclass(frozen=True, slots=True)
class _Planning:
    """What it takes to plan publication, so it can be done at either point."""

    workspace: WorkspaceGraph | None
    configuration: config_module.LadingConfig
    workspace_root: Path
    # Discovery must not update Cargo.lock when digests are expected.
    locked: bool = False
    # Runs workspace discovery when this plan has to discover it, so metadata
    # output passes through the same redaction as every other command.
    runner: CommandRunner | None = None

    def plan(self) -> PublishPlan:
        """Plan publication for the workspace (discovering it when needed)."""
        scope = (
            nullcontext()
            if self.runner is None
            else metadata_module.use_command_runner(self.runner)
        )
        with scope:
            workspace = _ensure_workspace(
                self.workspace, self.workspace_root, locked=self.locked
            )
        return plan_publication(
            workspace, self.configuration, workspace_root=self.workspace_root
        )


def _dispatch_reporting(
    plan: PublishPlan,
    preparation: publish_staging.PublishPreparation,
    options: publish_pipeline._PublishExecutionOptions,
    runner: CommandRunner,
) -> None:
    """Dispatch publication, logging the archive checks if it fails.

    A failed run returns no summary, so the outcomes decided before the
    failure would otherwise be visible only as scattered log lines.
    """
    try:
        publish_pipeline._dispatch_publication(
            plan, preparation, options=options, runner=runner
        )
    except PublishPreflightError:
        if options.verifier is not None:
            LOGGER.exception("\n".join(options.verifier.summary_lines()))
        raise


def run(
    workspace_root: Path,
    configuration: config_module.LadingConfig | None = None,
    workspace: WorkspaceGraph | None = None,
    *,
    options: PublishOptions | None = None,
) -> str:
    """Plan, stage, and execute publication from a workspace root.

    Parameters
    ----------
    workspace_root : Path
        Root directory of the Cargo workspace to publish.
    configuration : LadingConfig | None
        Optional configuration that overrides configuration loaded from the
        workspace.
    workspace : WorkspaceGraph | None
        Optional pre-discovered workspace graph for publication planning.
    options : PublishOptions | None, optional
        Runtime flags controlling preflight, staging, cleanup, and publication.
        When omitted, the default dry-run configuration is used.

    Returns
    -------
    str
        Human-readable publication plan and staged-workspace summary.
    """
    root_path = normalize_workspace_root(workspace_root)
    LOGGER.info("Starting publish workflow for workspace %s", root_path)
    effective_options = PublishOptions() if options is None else options
    _validate_publication_options(effective_options)
    active_configuration = _ensure_configuration(
        configuration or effective_options.configuration, root_path
    )
    command_runner = publish_digest_run.command_runner(effective_options)
    planning = _Planning(
        workspace or effective_options.workspace,
        active_configuration,
        root_path,
        locked=effective_options.expect_digests is not None,
        runner=publish_digest_run.discovery_runner(effective_options),
    )
    # With digests, the plan is needed to validate them, and that must happen
    # before the pre-flight, whose auxiliary builds can package crates.
    plan, verifier = publish_digest_run.digest_checks(
        effective_options, planning.plan, command_runner, root_path
    )
    publish_preflight._run_preflight_checks(
        root_path,
        _preflight_request(effective_options, active_configuration, command_runner),
    )
    plan = plan or planning.plan()
    # The staged tree lives no longer than this block: a publish that raises
    # or is interrupted must not leave its copy of the workspace behind.
    with publish_staging.staged_workspace(plan, options=effective_options) as staged:
        _apply_strip_patch_strategy(
            staged.staging_root, plan, active_configuration.publish.strip_patches
        )
        publish_digest_run.refuse_staged_defaults(
            plan, staged.staging_root, effective_options
        )
        _dispatch_reporting(
            plan,
            staged.preparation,
            _execution_options(effective_options, verifier),
            command_runner,
        )
        plan_message = format_plan(
            plan, strip_patches=active_configuration.publish.strip_patches
        )
    LOGGER.info("Publish workflow completed successfully for workspace %s", root_path)
    return _render_summary(plan_message, staged, verifier)
