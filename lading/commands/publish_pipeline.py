"""Execute per-crate publication pipelines for :mod:`lading.commands.publish`.

The coordinator prepares a plan and staged workspace, then delegates live and
dry-run sequencing here. This module applies package/publish actions through
an injected runner; :mod:`lading.commands.publish_execution` owns the concrete
subprocess adapter used by the default runner.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import time
import typing as typ

from lading.commands.cargo_output_adapter import (
    CargoAlreadyPublishedFailure,
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_already_published_failure,
    parse_index_lookup_failure,
)
from lading.commands.publish_errors import PublishError, PublishPreflightError
from lading.commands.publish_execution import (
    _CargoInvocation,
    _run_timed_cargo,
)
from lading.commands.publish_execution import (
    _invoke as _invoke,
)
from lading.commands.publish_index_check import (
    _format_cargo_failure_message,
    _IndexMissingVersionHandling,
)
from lading.commands.publish_index_check import (
    _handle_index_missing_version as _raw_handle_index_missing_version,
)
from lading.commands.publish_manifest import PublishPreparationError
from lading.commands.publish_sccache import SccacheSession, create_session
from lading.commands.publish_staging import (
    PublishPreparation,
    _resolve_staged_crate_root,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.publish_execution import _TimedCargoResult
    from lading.commands.publish_plan import PublishPlan
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceCrate

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class _PublishExecutionOptions:
    """Runtime flags that affect cargo package/publish invocations."""

    live: bool
    allow_dirty: bool
    allow_unpublished_workspace_deps: bool = False
    sccache_stats: bool = False
    sccache_stats_json: Path | None = None


@dc.dataclass(frozen=True, slots=True)
class _PublicationPipelineState:
    """Shared immutable state for cargo package and publish invocations."""

    plan: PublishPlan
    preparation: PublishPreparation
    options: _PublishExecutionOptions
    clock: cabc.Callable[[], float] = time.perf_counter
    sccache: SccacheSession | None = None

    def position(self, crate: WorkspaceCrate) -> str:
        """Return the crate's ``n/total`` position in publish order."""
        return f"{self.plan.publishable.index(crate) + 1}/{len(self.plan.publishable)}"


def _handle_index_missing_version(
    failure: CargoIndexLookupFailure,
    *,
    plan: PublishPlan,
    options: _PublishExecutionOptions,
) -> None:
    """Delegate using the phase-appropriate error class."""
    error_cls = (
        PublishError if failure.subcommand == "publish" else PublishPreflightError
    )
    _raw_handle_index_missing_version(
        failure,
        handling=_IndexMissingVersionHandling(
            plan=plan,
            options=options,
            logger=LOGGER,
        ),
        error_cls=error_cls,
    )


class _CrateAction(typ.Protocol):
    """Action applied to each crate in the publication pipeline."""

    def __call__(
        self,
        crate: WorkspaceCrate,
        state: _PublicationPipelineState,
        *,
        runner: CommandRunner,
    ) -> None:
        """Process a single staged crate from the pipeline."""


def _for_each_publishable_crate(
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
    action: _CrateAction,
) -> None:
    """Apply *action* to every publishable crate in pipeline order."""
    for crate in state.plan.publishable:
        action(crate, state, runner=runner)


def _package_publishable_crates(
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> None:
    """Package each publishable crate in order using the staged workspace."""
    _for_each_publishable_crate(state, runner=runner, action=_package_crate)


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
    position = state.position(crate)
    LOGGER.info("Running cargo package for crate %s (%s)", crate.name, position)
    result = _run_timed_cargo(
        _CargoInvocation(("cargo", "package", *package_args), crate_root, crate.name),
        runner=runner,
        clock=state.clock,
    )
    if state.sccache is not None:
        state.sccache.record(crate.name, "package", result.elapsed_seconds)
    if result.exit_code == 0:
        LOGGER.info(
            "Successfully packaged crate %s (%s) in %.1fs",
            crate.name,
            position,
            result.elapsed_seconds,
        )
        return
    lookup_failure = parse_index_lookup_failure(
        crate_name=crate.name,
        subcommand="package",
        result=CargoSubprocessResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        ),
    )
    if lookup_failure is not None:
        _handle_index_missing_version(lookup_failure, plan=plan, options=options)
        return
    message = _format_cargo_failure_message(
        "package", crate.name, result.exit_code, result.streams
    )
    LOGGER.error(message)
    raise PublishPreflightError(message)


def _publish_crates(
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> None:
    """Publish each crate in order, respecting dry-run vs live mode."""
    _for_each_publishable_crate(state, runner=runner, action=_publish_crate)


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
        "Running cargo publish%s for crate %s (%s)",
        "" if options.live else " --dry-run",
        crate.name,
        state.position(crate),
    )
    result = _run_timed_cargo(
        _CargoInvocation(
            ("cargo", "publish", *publish_args_tuple), crate_root, crate.name
        ),
        runner=runner,
        clock=state.clock,
    )
    if state.sccache is not None:
        state.sccache.record(crate.name, "publish", result.elapsed_seconds)
    _handle_publish_result(crate, result, state=state)


def _handle_publish_result(
    crate: WorkspaceCrate,
    result: _TimedCargoResult,
    *,
    state: _PublicationPipelineState,
) -> None:
    """Handle a completed ``cargo publish`` invocation."""
    plan = state.plan
    options = state.options
    exit_code, stdout, stderr = result.exit_code, result.stdout, result.stderr
    if exit_code == 0:
        success_message = (
            "Successfully published crate %s (%s) in %.1fs"
            if options.live
            else "Dry-run publish succeeded for crate %s (%s) in %.1fs"
        )
        LOGGER.info(
            success_message, crate.name, state.position(crate), result.elapsed_seconds
        )
        return
    cargo_result = CargoSubprocessResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )
    already_published_failure: CargoAlreadyPublishedFailure | None = (
        parse_already_published_failure(cargo_result)
    )
    if already_published_failure is not None:
        LOGGER.warning(
            "Crate %s @ %s is already published; skipping",
            crate.name,
            crate.version,
        )
        return
    lookup_failure = parse_index_lookup_failure(
        crate_name=crate.name,
        subcommand="publish",
        result=cargo_result,
    )
    if lookup_failure is not None:
        # cargo publish --dry-run packages internally and hits the same
        # crates.io index lookup as cargo package, so honour the override
        # consistently across both phases.
        _handle_index_missing_version(lookup_failure, plan=plan, options=options)
        return

    message = _format_cargo_failure_message(
        "publish", crate.name, exit_code, (stdout, stderr)
    )
    LOGGER.error(message)
    raise PublishError(message)


def _execute_live_publication_pipeline(
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> None:
    """Package and publish each crate before moving to the next crate."""
    plan = state.plan
    completed: list[str] = []
    for crate in plan.publishable:
        LOGGER.info("Live pipeline: starting crate %s", crate.name)
        try:
            _package_crate(crate, state, runner=runner)
            _publish_crate(crate, state, runner=runner)
        except PublishPreparationError as exc:
            # Preparation failures escape the preflight/publish error taxonomy;
            # normalize them so the live pipeline reports a single abort class.
            LOGGER.exception(*_live_pipeline_abort_log_args(crate, completed, plan))
            raise PublishPreflightError(str(exc)) from exc
        except PublishPreflightError:
            LOGGER.exception(*_live_pipeline_abort_log_args(crate, completed, plan))
            raise
        LOGGER.info("Live pipeline: completed crate %s", crate.name)
        completed.append(crate.name)


def _live_pipeline_abort_log_args(
    crate: WorkspaceCrate,
    completed: cabc.Sequence[str],
    plan: PublishPlan,
) -> tuple[str, str, int, int, str]:
    """Return structured log arguments for a live-pipeline abort."""
    return (
        "Live pipeline: aborted on crate %s — %d/%d crates completed (%s)",
        crate.name,
        len(completed),
        len(plan.publishable),
        ", ".join(completed) if completed else "none",
    )


def _run_dry_run_phase(
    phase_name: str,
    action: cabc.Callable[[], None],
) -> None:
    """Run one dry-run phase, normalising preparation errors at the boundary."""
    try:
        action()
    except PublishPreparationError as exc:
        LOGGER.exception("Dry-run pipeline: %s phase failed", phase_name)
        raise PublishPreflightError(str(exc)) from exc
    except PublishPreflightError:
        LOGGER.exception("Dry-run pipeline: %s phase failed", phase_name)
        raise


def _dispatch_publication(
    plan: PublishPlan,
    preparation: PublishPreparation,
    *,
    options: _PublishExecutionOptions,
    runner: CommandRunner,
) -> None:
    """Route to the live or dry-run publication pipeline.

    Design note (issue #72): this helper is more than a relocated branch.
    It owns the operator-facing pipeline-mode log line, sequences the
    dry-run two-phase pipeline (package everything, then publish
    everything), and gives tests a single seam to exercise mode dispatch
    without driving ``run()`` end to end. Inlining it would push ``run()``
    back toward the complexity ceiling that prompted the extraction.
    """
    sccache = create_session(options, runner=runner, workspace_root=plan.workspace_root)
    if sccache is not None:
        sccache.begin()
    state = _PublicationPipelineState(plan, preparation, options, sccache=sccache)
    try:
        if options.live:
            LOGGER.info("Publication mode: live (interleaved per-crate pipeline)")
            _execute_live_publication_pipeline(state, runner=runner)
            return

        LOGGER.info("Publication mode: dry-run (batched two-phase pipeline)")
        _run_dry_run_phase(
            "packaging", lambda: _package_publishable_crates(state, runner=runner)
        )
        LOGGER.info("Dry-run pipeline: packaging complete; starting publish phase")
        _run_dry_run_phase("publish", lambda: _publish_crates(state, runner=runner))
    finally:
        if sccache is not None:
            sccache.finish()
