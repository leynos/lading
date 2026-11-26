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
from pathlib import Path

from lading import config as config_module
from lading.commands import (
    publish_digests,
    publish_pipeline,
    publish_preflight,
    publish_registry,
    publish_staging,
)
from lading.commands.publish_archive_check import ArchiveVerifier
from lading.commands.publish_errors import PublishPreflightError
from lading.commands.publish_manifest import _apply_strip_patch_strategy
from lading.commands.publish_plan import PublishPlan, format_plan, plan_publication
from lading.commands.publish_plan import PublishPlanError as PublishPlanError
from lading.utils.path import normalize_workspace_root

if typ.TYPE_CHECKING:
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
    default_registry : str | None
        Registry Cargo publishes to when a crate names none, as read from
        ``CARGO_REGISTRY_DEFAULT`` by the command-line entry point. Only
        consulted with ``expect_digests``, which supports crates.io alone.
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
    default_registry: str | None = None
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
    if options.expect_digests is not None:
        publish_digests.require_crates_io_default(options.default_registry)
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
    )


def _load_expected_digests(
    options: PublishOptions, workspace_root: Path
) -> publish_digests.ExpectedDigests | None:
    """Read the ``--expect-digests`` file, if one was given, before any work."""
    if options.expect_digests is None:
        return None
    return publish_digests.load_expected_digests(
        workspace_root / options.expect_digests
    )


def _archive_verifier(
    expected: publish_digests.ExpectedDigests | None,
    plan: PublishPlan,
    options: PublishOptions,
    runner: CommandRunner,
) -> ArchiveVerifier | None:
    """Check the digests against the plan and build the run's verifier."""
    if expected is not None:
        publish_digests.validate_against_plan(expected, plan)
        publish_digests.require_crates_io_crates(plan)
        if options.published_digest_fetcher is None:
            return ArchiveVerifier(expected, runner)
        return ArchiveVerifier(expected, runner, options.published_digest_fetcher)
    return None


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
    command_runner = effective_options.command_runner or publish_pipeline._invoke
    # Read before the pre-flight, which builds the workspace: a typo in the
    # digest file should cost seconds, not a verify build.
    expected_digests = _load_expected_digests(effective_options, root_path)
    publish_preflight._run_preflight_checks(
        root_path,
        _preflight_request(effective_options, active_configuration, command_runner),
    )
    active_workspace = _ensure_workspace(
        workspace or effective_options.workspace, root_path
    )
    plan = plan_publication(
        active_workspace, active_configuration, workspace_root=root_path
    )
    verifier = _archive_verifier(
        expected_digests, plan, effective_options, command_runner
    )
    # The staged tree lives no longer than this block: a publish that raises
    # or is interrupted must not leave its copy of the workspace behind.
    with publish_staging.staged_workspace(plan, options=effective_options) as staged:
        _apply_strip_patch_strategy(
            staged.staging_root, plan, active_configuration.publish.strip_patches
        )
        publish_pipeline._dispatch_publication(
            plan,
            staged.preparation,
            options=_execution_options(effective_options, verifier),
            runner=command_runner,
        )
        plan_message = format_plan(
            plan, strip_patches=active_configuration.publish.strip_patches
        )
    LOGGER.info("Publish workflow completed successfully for workspace %s", root_path)
    return _render_summary(plan_message, staged, verifier)
