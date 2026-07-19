"""Handle cargo index-lookup failures during publish workflows.

This module keeps downgrade logic for missing registry versions separate from the
publish command orchestration. ``publish.py`` imports these helpers while running
``cargo package`` and ``cargo publish`` so both phases share the same
index-missing-version failure formatting and override handling.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ

from lading.utils import metrics
from lading.utils.process import with_detail

if typ.TYPE_CHECKING:
    from lading.commands.cargo_output_adapter import CargoIndexLookupFailure
    from lading.commands.publish import _PublishExecutionOptions
    from lading.commands.publish_plan import PublishPlan

# Counter incremented each time an index-lookup failure is downgraded to a
# warning because the missing dependency is in the publish plan (issue #68).
INDEX_LOOKUP_DOWNGRADE_METRIC = "publish.index_lookup_downgrade"


@dc.dataclass(frozen=True, slots=True)
class _IndexMissingVersionFailure:
    """Shared context for reporting a fatal index-lookup failure."""

    error_cls: type[Exception]
    failure: CargoIndexLookupFailure
    failure_message: str
    logger: logging.Logger


@dc.dataclass(frozen=True, slots=True)
class _IndexMissingVersionHandling:
    """Dependencies needed to classify and report an index-lookup failure."""

    plan: PublishPlan
    options: _PublishExecutionOptions
    logger: logging.Logger


def _format_cargo_failure_message(
    command: str,
    crate_name: str,
    exit_code: int,
    output: tuple[str, str],
) -> str:
    """Format a human-readable error string for a failed cargo invocation.

    ``output`` is a ``(stdout, stderr)`` pair; ``stderr`` is preferred as the
    detail source and falls back to ``stdout`` when stderr is empty. Using a
    single function for all cargo failure messages keeps the format identical
    across the packaging and publish phases, which makes snapshot assertions
    stable.
    """
    stdout, stderr = output
    return with_detail(
        f"cargo {command} failed for crate {crate_name} with exit code {exit_code}",
        stdout,
        stderr,
    )


def _raise_name_extraction_failure(
    context: _IndexMissingVersionFailure,
) -> typ.NoReturn:
    """Log and raise when the missing dependency name cannot be extracted."""
    context.logger.warning(
        "cargo %s for crate %s matched index-missing-version markers "
        "but the dependency name could not be extracted; treating as fatal",
        context.failure.subcommand,
        context.failure.crate_name,
    )
    raise context.error_cls(context.failure_message)


def _format_missing_dependency_failure(
    failure: str,
    *,
    missing_name: str,
    reason: str,
    guidance: str,
) -> str:
    """Return the shared fatal message shape for dependency index misses."""
    return f"{failure}\n\ndependency {missing_name!r} {reason}. {guidance}"


def _log_missing_dependency_failure(
    logger: logging.Logger,
    lookup_failure: CargoIndexLookupFailure,
    *,
    missing_name: str,
    detail: str,
) -> None:
    """Emit the shared warning shape for fatal dependency index misses."""
    logger.warning(
        "cargo %s for crate %s failed due to unindexed dependency %r %s",
        lookup_failure.subcommand,
        lookup_failure.crate_name,
        missing_name,
        detail,
    )


def _raise_out_of_plan_dependency(
    context: _IndexMissingVersionFailure,
    *,
    missing_name: str,
) -> typ.NoReturn:
    """Log and raise when the unindexed dependency is outside the publish plan."""
    message = _format_missing_dependency_failure(
        context.failure_message,
        missing_name=missing_name,
        reason=(
            "is not part of the current publish plan, so the unpublished "
            "workspace dependency override cannot help"
        ),
        guidance="Publish or index the dependency first.",
    )
    _log_missing_dependency_failure(
        context.logger,
        context.failure,
        missing_name=missing_name,
        detail="which is not in the current publish plan; cannot continue",
    )
    raise context.error_cls(message)


def _raise_out_of_order_dependency(
    context: _IndexMissingVersionFailure,
    *,
    missing_name: str,
) -> typ.NoReturn:
    """Log and raise when a planned dependency is published too late."""
    message = _format_missing_dependency_failure(
        context.failure_message,
        missing_name=missing_name,
        reason=(
            f"appears after crate {context.failure.crate_name!r} in publish "
            "order, so it will not be available when this crate is published"
        ),
        guidance=(
            "Adjust publish.order so the dependency comes first, or omit "
            "publish.order and rely on dependency-derived topological sorting."
        ),
    )
    _log_missing_dependency_failure(
        context.logger,
        context.failure,
        missing_name=missing_name,
        detail=(
            "which appears after the current crate in publish order; cannot continue"
        ),
    )
    raise context.error_cls(message)


def _raise_self_dependency(
    context: _IndexMissingVersionFailure,
    *,
    missing_name: str,
) -> typ.NoReturn:
    """Log and raise when cargo reports the current crate as its dependency."""
    message = _format_missing_dependency_failure(
        context.failure_message,
        missing_name=missing_name,
        reason=(
            f"is the same crate as {context.failure.crate_name!r}, so the "
            "publish plan cannot make it available before itself"
        ),
        guidance="Remove the self-dependency from the crate manifest.",
    )
    _log_missing_dependency_failure(
        context.logger,
        context.failure,
        missing_name=missing_name,
        detail="which is the current crate; cannot continue",
    )
    raise context.error_cls(message)


def _raise_unpublished_dependency_override_required(
    context: _IndexMissingVersionFailure,
    *,
    missing_name: str,
) -> typ.NoReturn:
    """Log and raise when the unpublished dependency override is disabled.

    Called when the missing dependency is part of the publish plan but the
    caller has not opted into the dry-run override that downgrades the failure
    to a warning.
    """
    message = _format_missing_dependency_failure(
        context.failure_message,
        missing_name=missing_name,
        reason="is scheduled in this publish run but is not yet on crates.io",
        guidance=(
            "Enable the dry-run unpublished workspace dependency override, or "
            "follow the staged-publish workaround."
        ),
    )
    _log_missing_dependency_failure(
        context.logger,
        context.failure,
        missing_name=missing_name,
        detail=(
            "(in plan); enable the dry-run unpublished workspace dependency "
            "override to downgrade to a warning, or follow the staged-publish "
            "workaround"
        ),
    )
    raise context.error_cls(message)


def _canonical_crate_name(name: str) -> str:
    """Return the canonical crate name by normalising hyphens to underscores.

    Cargo error diagnostics report crate names using hyphens (e.g.
    ``my-crate``), whereas ``Cargo.toml`` manifests typically record the same
    package under underscores (e.g. ``my_crate``). Normalising both sides of
    any membership comparison prevents false out-of-plan classifications that
    would block the downgrade override.
    """
    return name.replace("-", "_")


class _DependencyPlacement(typ.NamedTuple):
    """Resolved publish-order placement for a downgraded index-lookup failure.

    Attributes
    ----------
    current_index:
        Publish-order index of the crate whose cargo invocation failed.
    missing_index:
        Publish-order index of the missing sibling dependency.
    missing_canonical_name:
        Canonicalised (underscore-only) name of the missing dependency.
    """

    current_index: int
    missing_index: int
    missing_canonical_name: str


def _validate_dependency_placement(
    context: _IndexMissingVersionFailure,
    handling: _IndexMissingVersionHandling,
    missing_name: str,
) -> _DependencyPlacement:
    """Resolve both crate indexes and raise on all fatal placement conditions.

    Returns a :class:`_DependencyPlacement` when the missing dependency is in
    the plan and is ordered before the current crate. Raises the context
    exception class for every other case.
    """
    publishable_name_indexes: dict[str, int] = {
        _canonical_crate_name(entry.name): index
        for index, entry in enumerate(handling.plan.publishable)
    }
    canonical_current = _canonical_crate_name(context.failure.crate_name)
    current_index = publishable_name_indexes.get(canonical_current)
    if current_index is None:
        message = (
            f"cargo {context.failure.subcommand} failed for crate "
            f"{context.failure.crate_name}, but that crate is not part "
            "of the current publish plan."
        )
        raise context.error_cls(message)

    missing_canonical_name = _canonical_crate_name(missing_name)
    handling.logger.debug(
        "index-missing-version handler: current crate %r at publish-order index %d; "
        "missing dependency canonical name %r",
        context.failure.crate_name,
        current_index,
        missing_canonical_name,
    )
    missing_index = publishable_name_indexes.get(missing_canonical_name)
    handling.logger.debug(
        "index-missing-version handler: missing dependency %r resolved to "
        "publish-order index %s",
        missing_name,
        missing_index if missing_index is not None else "<not in plan>",
    )
    if missing_index is None:
        _raise_out_of_plan_dependency(context, missing_name=missing_name)
    if missing_index == current_index:
        _raise_self_dependency(context, missing_name=missing_name)
    if missing_index > current_index:
        _raise_out_of_order_dependency(context, missing_name=missing_name)
    return _DependencyPlacement(current_index, missing_index, missing_canonical_name)


def _emit_downgrade_success(
    handling: _IndexMissingVersionHandling,
    failure: CargoIndexLookupFailure,
    *,
    missing_name: str,
    placement: _DependencyPlacement,
) -> None:
    """Increment the downgrade metric and emit the associated log messages.

    Called only when the missing dependency is in the publish plan and the
    caller has opted into the unpublished workspace dependency override.
    ``placement`` is the :class:`_DependencyPlacement` resolved by
    ``_validate_dependency_placement``.
    """
    metrics.increment_counter(
        INDEX_LOOKUP_DOWNGRADE_METRIC,
        subcommand=failure.subcommand,
        missing_crate=missing_name,
    )
    handling.logger.warning(
        "cargo %s for crate %s could not resolve sibling dependency %s "
        "from crates.io; continuing because the unpublished workspace "
        "dependency override is enabled",
        failure.subcommand,
        failure.crate_name,
        missing_name,
    )
    handling.logger.debug(
        "canonicalised dependency name %r -> %r",
        missing_name,
        placement.missing_canonical_name,
    )
    handling.logger.info(
        "Downgraded cargo %s failure for crate %s (index %d) because "
        "dependency %s (index %d) is part of the publish plan and the "
        "unpublished workspace dependency override is enabled",
        failure.subcommand,
        failure.crate_name,
        placement.current_index,
        missing_name,
        placement.missing_index,
    )


def _handle_index_missing_version(
    failure: CargoIndexLookupFailure,
    *,
    handling: _IndexMissingVersionHandling,
    error_cls: type[Exception],
) -> None:
    """Handle a cargo failure caused by an unindexed sibling dependency.

    Raises ``error_cls`` (chosen by the caller) unless the missing dependency
    is in the current publish plan and the caller opted into the dry-run
    override.
    """
    failure_message = _format_cargo_failure_message(
        failure.subcommand,
        failure.crate_name,
        failure.exit_code,
        (failure.stdout, failure.stderr),
    )
    context = _IndexMissingVersionFailure(
        error_cls=error_cls,
        failure=failure,
        failure_message=failure_message,
        logger=handling.logger,
    )

    missing_name = failure.missing_dependency_name
    if missing_name is not None:
        _downgrade_or_raise(
            failure, context=context, handling=handling, missing_name=missing_name
        )
        return
    _raise_name_extraction_failure(context)
    return


def _downgrade_or_raise(
    failure: CargoIndexLookupFailure,
    *,
    context: _IndexMissingVersionFailure,
    handling: _IndexMissingVersionHandling,
    missing_name: str,
) -> None:
    """Downgrade a planned-dependency failure or raise when overrides forbid it."""
    placement = _validate_dependency_placement(context, handling, missing_name)

    handling.logger.debug(
        "index-missing-version handler: allow_unpublished_workspace_deps=%r "
        "for crate %r; %s",
        handling.options.allow_unpublished_workspace_deps,
        failure.crate_name,
        "will raise"
        if not handling.options.allow_unpublished_workspace_deps
        else "will downgrade to warning",
    )

    if handling.options.allow_unpublished_workspace_deps:
        _emit_downgrade_success(
            handling,
            failure,
            missing_name=missing_name,
            placement=placement,
        )
        return
    _raise_unpublished_dependency_override_required(context, missing_name=missing_name)
    return
