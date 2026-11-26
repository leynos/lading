"""Classify failed ``cargo package`` and ``cargo publish`` invocations.

:mod:`lading.commands.publish_pipeline` hands a non-zero cargo result here. The
handlers either downgrade it (an in-plan index miss, or a version that is
already published), or raise the error class for the phase that failed. They
are kept apart from the sequencing code so the pipeline stays readable and
within the file-size limit.
"""

import logging
import typing as typ

from lading.commands.cargo_output_adapter import (
    CargoAlreadyPublishedFailure,
    CargoIndexLookupFailure,
    CargoSubprocessResult,
    parse_already_published_failure,
    parse_index_lookup_failure,
)
from lading.commands.publish_errors import PublishError, PublishPreflightError
from lading.commands.publish_index_check import (
    _format_cargo_failure_message,
    _IndexMissingVersionHandling,
)
from lading.commands.publish_index_check import (
    _handle_index_missing_version as _raw_handle_index_missing_version,
)

if typ.TYPE_CHECKING:
    from lading.commands.publish_execution import _TimedCargoResult
    from lading.commands.publish_pipeline import (
        _PublicationPipelineState,
        _PublishExecutionOptions,
    )
    from lading.commands.publish_plan import PublishPlan
    from lading.workspace import WorkspaceCrate

LOGGER = logging.getLogger(__name__)


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


def _handle_package_failure(
    crate: WorkspaceCrate,
    result: _TimedCargoResult,
    *,
    state: _PublicationPipelineState,
) -> None:
    """Downgrade an in-plan index miss from ``cargo package``, else raise."""
    options = state.options
    lookup_failure = parse_index_lookup_failure(
        crate_name=crate.name,
        subcommand="package",
        result=CargoSubprocessResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        ),
    )
    if lookup_failure is None:
        message = _format_cargo_failure_message(
            "package", crate.name, result.exit_code, result.streams
        )
        LOGGER.error(message)
        raise PublishPreflightError(message)
    _handle_index_missing_version(lookup_failure, plan=state.plan, options=options)
    if options.verifier is not None:
        options.verifier.mark_unverified(
            crate, "cargo package was downgraded to a warning; no archive built"
        )


def _skip_already_published(
    crate: WorkspaceCrate, options: _PublishExecutionOptions
) -> None:
    """Compare a version Cargo reports as existing, or log and skip it."""
    if options.verifier is not None:
        options.verifier.confirm_already_published(crate)
        return
    LOGGER.warning(
        "Crate %s @ %s is already published; skipping", crate.name, crate.version
    )


def _handle_publish_failure(
    crate: WorkspaceCrate,
    result: _TimedCargoResult,
    *,
    state: _PublicationPipelineState,
) -> None:
    """Classify a failed ``cargo publish``: skip, downgrade, or raise."""
    cargo_result = CargoSubprocessResult(
        exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr
    )
    already_published: CargoAlreadyPublishedFailure | None = (
        parse_already_published_failure(cargo_result)
    )
    if already_published is not None:
        _skip_already_published(crate, state.options)
        return
    lookup_failure = parse_index_lookup_failure(
        crate_name=crate.name, subcommand="publish", result=cargo_result
    )
    if lookup_failure is not None:
        # cargo publish --dry-run packages internally and hits the same
        # crates.io index lookup as cargo package, so honour the override
        # consistently across both phases.
        _handle_index_missing_version(
            lookup_failure, plan=state.plan, options=state.options
        )
        if state.options.verifier is not None:
            state.options.verifier.mark_unverified(
                crate, "cargo publish --dry-run was downgraded to a warning"
            )
        return
    message = _format_cargo_failure_message(
        "publish", crate.name, result.exit_code, result.streams
    )
    LOGGER.error(message)
    raise PublishError(message)
