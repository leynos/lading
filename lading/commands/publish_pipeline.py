"""Execute per-crate publication pipelines for :mod:`lading.commands.publish`.

The coordinator prepares a plan and staged workspace, then delegates live and
dry-run sequencing here. This module applies package/publish actions through
an injected runner; :mod:`lading.commands.publish_execution` owns the concrete
subprocess adapter used by the default runner.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import time
import typing as typ

from lading.commands import publish_failures
from lading.commands.publish_archive_check import PACKAGED, UPLOADED
from lading.commands.publish_errors import PublishPreflightError
from lading.commands.publish_execution import (
    _CargoInvocation,
    _run_timed_cargo,
)
from lading.commands.publish_execution import (
    _invoke as _invoke,
)
from lading.commands.publish_manifest import PublishPreparationError
from lading.commands.publish_sccache import SccacheSession, create_session
from lading.commands.publish_staging import (
    PublishPreparation,
    _resolve_staged_crate_root,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.publish_archive_check import ArchiveVerifier
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
    # Set when expected digests are given. It rides with the flags because
    # `--locked` follows from it: a digest only means something against a
    # fixed lockfile, so both cargo commands then refuse to update the lock.
    verifier: ArchiveVerifier | None = None

    @property
    def is_locked(self) -> bool:
        """Whether cargo must be told not to update ``Cargo.lock``."""
        return self.verifier is not None


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


def _cargo_flags(options: _PublishExecutionOptions) -> tuple[str, ...]:
    """Return the flags ``cargo package`` and ``cargo publish`` share."""
    return (
        *(("--allow-dirty",) if options.allow_dirty else ()),
        # With digests, a digest only means something against a fixed lockfile
        # and a fixed registry. `--registry crates-io` stops Cargo's own
        # configuration (`[registry] default`) from sending the upload, or the
        # dependency resolution, anywhere lading cannot compare against.
        *(("--locked", "--registry", "crates-io") if options.is_locked else ()),
    )


def _run_recorded_cargo(
    invocation: _CargoInvocation,
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> _TimedCargoResult:
    """Run one timed cargo invocation and record it with the sccache session."""
    result = _run_timed_cargo(invocation, runner=runner, clock=state.clock)
    if state.sccache is not None:
        state.sccache.record(
            invocation.crate_name, invocation.subcommand, result.elapsed_seconds
        )
    return result


def _package_crate(
    crate: WorkspaceCrate,
    state: _PublicationPipelineState,
    *,
    runner: CommandRunner,
) -> None:
    """Package one publishable crate using the staged workspace.

    Under ``--expect-digests`` a version already on crates.io is compared first
    and, when identical, not packaged at all: its published bytes are what
    matter, whatever a local build would produce.
    """
    plan = state.plan
    options = state.options
    if options.verifier is not None and options.verifier.resolve_published(crate):
        return
    package_args = _cargo_flags(options)
    crate_root = _resolve_staged_crate_root(crate, plan, state.preparation.staging_root)
    position = state.position(crate)
    LOGGER.info("Running cargo package for crate %s (%s)", crate.name, position)
    result = _run_recorded_cargo(
        _CargoInvocation(("cargo", "package", *package_args), crate_root, crate.name),
        state,
        runner=runner,
    )
    if result.exit_code != 0:
        publish_failures._handle_package_failure(crate, result, state=state)
        return
    LOGGER.info(
        "Successfully packaged crate %s (%s) in %.1fs",
        crate.name,
        position,
        result.elapsed_seconds,
    )
    if options.verifier is not None:
        options.verifier.check_archive(crate, crate_root, PACKAGED)


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
    publish_args = (*_cargo_flags(options), *(() if options.live else ("--dry-run",)))
    crate_root = _resolve_staged_crate_root(crate, plan, state.preparation.staging_root)
    if options.verifier is not None and options.verifier.is_already_published(crate):
        # Found identical on crates.io before packaging: nothing to upload, so
        # a rerun of an interrupted release resumes safely.
        return
    LOGGER.info(
        "Running cargo publish%s for crate %s (%s)",
        "" if options.live else " --dry-run",
        crate.name,
        state.position(crate),
    )
    result = _run_recorded_cargo(
        _CargoInvocation(("cargo", "publish", *publish_args), crate_root, crate.name),
        state,
        runner=runner,
    )
    _handle_publish_result(crate, result, state=state)


def _handle_publish_result(
    crate: WorkspaceCrate,
    result: _TimedCargoResult,
    *,
    state: _PublicationPipelineState,
) -> None:
    """Handle a completed ``cargo publish`` invocation."""
    options = state.options
    if result.exit_code != 0:
        publish_failures._handle_publish_failure(crate, result, state=state)
        return
    success_message = (
        "Successfully published crate %s (%s) in %.1fs"
        if options.live
        else "Dry-run publish succeeded for crate %s (%s) in %.1fs"
    )
    LOGGER.info(
        success_message, crate.name, state.position(crate), result.elapsed_seconds
    )
    if options.live and options.verifier is not None:
        # The upload cannot be undone, so a difference here fails the run
        # loudly rather than being repaired.
        options.verifier.check_archive(
            crate,
            _resolve_staged_crate_root(
                crate, state.plan, state.preparation.staging_root
            ),
            UPLOADED,
        )


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
