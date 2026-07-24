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
import typing as typ

from lading.commands.cargo_output_adapter import (
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_index_lookup_failure,
)
from lading.commands.publish_errors import PublishError, PublishPreflightError
from lading.commands.publish_execution import _invoke as _invoke
from lading.commands.publish_index_check import (
    _format_cargo_failure_message,
    _IndexMissingVersionHandling,
)
from lading.commands.publish_index_check import (
    _handle_index_missing_version as _raw_handle_index_missing_version,
)
from lading.commands.publish_manifest import PublishPreparationError
from lading.commands.publish_staging import (
    PublishPreparation,
    _resolve_staged_crate_root,
)

if typ.TYPE_CHECKING:
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


@dc.dataclass(frozen=True, slots=True)
class _PublicationPipelineState:
    """Shared publish state for cargo package and publish invocations.

    Design note (issue #72): this bundle is deliberate. The per-crate
    helpers (``_package_crate``, ``_publish_crate``) would otherwise need
    ``plan``, ``preparation``, and ``options`` threaded individually,
    pushing their signatures past the argument-count lint ceiling and
    inviting positional mix-ups. The three fields are constructed together
    in each pipeline entry point and are immutable for the pipeline's
    lifetime, which is the invariant the dataclass enforces.
    """

    plan: PublishPlan
    preparation: PublishPreparation
    options: _PublishExecutionOptions


def _handle_index_missing_version(
    failure: CargoIndexLookupFailure,
    *,
    plan: PublishPlan,
    options: _PublishExecutionOptions,
) -> None:
    """Pick the phase-appropriate error class and delegate to the helper.

    Resolve the error class here based on the cargo subcommand and pass it
    through to the relocated implementation in ``publish_index_check``.
    """
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
    plan: PublishPlan,
    preparation: PublishPreparation,
    *,
    options: _PublishExecutionOptions,
    runner: CommandRunner,
) -> None:
    """Package each publishable crate in order using the staged workspace."""
    _for_each_publishable_crate(
        _PublicationPipelineState(plan, preparation, options),
        runner=runner,
        action=_package_crate,
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
    lookup_failure = parse_index_lookup_failure(
        crate_name=crate.name,
        subcommand="package",
        result=CargoSubprocessResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        ),
    )
    if lookup_failure is not None:
        _handle_index_missing_version(lookup_failure, plan=plan, options=options)
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
    _for_each_publishable_crate(
        _PublicationPipelineState(plan, preparation, options),
        runner=runner,
        action=_publish_crate,
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
        crate, (exit_code, stdout, stderr), plan=plan, options=options
    )


def _handle_publish_result(
    crate: WorkspaceCrate,
    output: tuple[int, str, str],
    *,
    plan: PublishPlan,
    options: _PublishExecutionOptions,
) -> None:
    """Handle a completed ``cargo publish`` invocation."""
    exit_code, stdout, stderr = output
    if exit_code == 0:
        success_message = (
            "Successfully published crate %s"
            if options.live
            else "Dry-run publish succeeded for crate %s"
        )
        LOGGER.info(success_message, crate.name)
        return
    if _is_already_published_error(exit_code, stdout, stderr):
        LOGGER.warning(
            "Crate %s @ %s is already published; skipping",
            crate.name,
            crate.version,
        )
        return
    lookup_failure = parse_index_lookup_failure(
        crate_name=crate.name,
        subcommand="publish",
        result=CargoSubprocessResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        ),
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
            _package_crate(crate, state, runner=runner)
            _publish_crate(crate, state, runner=runner)
        except PublishPreparationError as exc:
            # Preparation failures escape the preflight/publish error taxonomy;
            # normalise them so the live pipeline reports a single abort class.
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
    """Run one dry-run phase, normalising staging errors at the boundary.

    The shared error handling keeps the two-phase dispatcher focused on phase
    ordering while retaining its single public preflight-error contract.
    """
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
    if options.live:
        LOGGER.info("Publication mode: live (interleaved per-crate pipeline)")
        _execute_live_publication_pipeline(
            plan,
            preparation,
            options=options,
            runner=runner,
        )
        return

    LOGGER.info("Publication mode: dry-run (batched two-phase pipeline)")
    _run_dry_run_phase(
        "packaging",
        lambda: _package_publishable_crates(
            plan,
            preparation,
            options=options,
            runner=runner,
        ),
    )
    LOGGER.info("Dry-run pipeline: packaging complete; starting publish phase")
    _run_dry_run_phase(
        "publish",
        lambda: _publish_crates(
            plan,
            preparation,
            runner=runner,
            options=options,
        ),
    )
