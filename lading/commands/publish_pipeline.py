"""Per-crate publication pipeline for ``lading publish``.

Extracted from :mod:`lading.commands.publish` (issue #108). This module owns
the cargo package/publish invocations for each crate, the result
classification for completed publishes, and the live versus dry-run pipeline
dispatch. :mod:`lading.commands.publish` re-exports these helpers so the
historical ``publish._package_crate``-style access used by tests keeps
resolving.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ

from lading.commands.publish_errors import PublishError, PublishPreflightError
from lading.commands.publish_index_check import (
    _CargoInvocation,
    _format_cargo_failure_message,
    _IndexMissingVersionHandling,
    _is_index_missing_version_error,
)
from lading.commands.publish_index_check import (
    _handle_index_missing_version as _raw_handle_index_missing_version,
)
from lading.commands.publish_manifest import PublishPreparationError

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.publish import PublishPreparation
    from lading.commands.publish_plan import PublishPlan
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceCrate

LOGGER = logging.getLogger("lading.commands.publish")


@dc.dataclass(frozen=True, slots=True)
class _PublishExecutionOptions:
    """Runtime flags that affect cargo package/publish invocations."""

    live: bool
    allow_dirty: bool
    allow_unpublished_workspace_deps: bool = False


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


@dc.dataclass(frozen=True, slots=True)
class _PublicationPipelineState:
    """Shared publish state for cargo package and publish invocations."""

    plan: PublishPlan
    preparation: PublishPreparation
    options: _PublishExecutionOptions


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
        invocation,
        handling=_IndexMissingVersionHandling(
            plan=plan,
            options=options,
            logger=LOGGER,
        ),
        error_cls=error_cls,
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
