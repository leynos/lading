"""Handle cargo index-lookup failures during publish workflows.

This module keeps the error detection and downgrade logic for missing registry
versions separate from the publish command orchestration. ``publish.py``
imports these helpers while running ``cargo package`` and ``cargo publish`` so
both phases share the same index-missing-version checks, dependency-name
extraction, failure formatting, and override handling.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import re
import typing as typ

if typ.TYPE_CHECKING:
    from lading.commands.publish import _PublishExecutionOptions
    from lading.commands.publish_plan import PublishPlan

_INDEX_MISSING_VERSION_MARKERS: tuple[str, ...] = (
    "failed to select a version for the requirement",
    "location searched: crates.io index",
)

# Capture the dependency crate name from cargo's index-lookup error, e.g.
#   failed to select a version for the requirement `inner_crate = "^0.8.0"`
_INDEX_MISSING_VERSION_NAME_PATTERN = re.compile(
    "failed to select a version for the requirement [`'\"]"  # noqa: RUF039 - keeps escaped quote pattern for cargo diagnostics
    r"(?P<name>[A-Za-z0-9_][A-Za-z0-9_-]*)\s*=",
    re.IGNORECASE,
)


def _is_index_missing_version_error(exit_code: int, stdout: str, stderr: str) -> bool:
    """Return True when ``cargo package`` failed due to an unindexed dependency.

    The cargo command exits non-zero with output that simultaneously mentions
    the version selection failure and the crates.io index. Both markers are
    required to minimize false positives from unrelated lookup failures.
    """
    if exit_code == 0:
        return False
    haystack = f"{stdout}\n{stderr}".lower()
    return all(marker in haystack for marker in _INDEX_MISSING_VERSION_MARKERS)


def _extract_missing_dependency_name(stdout: str, stderr: str) -> str | None:
    """Return the missing dependency crate name parsed from cargo output.

    Searches ``stderr`` before ``stdout`` using
    ``_INDEX_MISSING_VERSION_NAME_PATTERN``. Returns ``None`` when neither
    stream contains a parseable dependency name.
    """
    for stream in (stderr, stdout):
        match = _INDEX_MISSING_VERSION_NAME_PATTERN.search(stream)
        if match is not None:
            return match.group("name")
    return None


@dc.dataclass(frozen=True, slots=True)
class _CargoInvocation:
    """Identifies a cargo invocation that produced an index-lookup failure."""

    crate_name: str
    subcommand: typ.Literal["package", "publish"]
    output: tuple[int, str, str]


@dc.dataclass(frozen=True, slots=True)
class _IndexMissingVersionFailure:
    """Shared context for reporting a fatal index-lookup failure."""

    error_cls: type[Exception]
    invocation: _CargoInvocation
    failure: str
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
    detail = (stderr or stdout).strip()
    message = (
        f"cargo {command} failed for crate {crate_name} with exit code {exit_code}"
    )
    if detail:
        message = f"{message}: {detail}"
    return message


def _raise_name_extraction_failure(
    context: _IndexMissingVersionFailure,
) -> typ.NoReturn:
    """Log and raise when the missing dependency name cannot be extracted."""
    context.logger.warning(
        "cargo %s for crate %s matched index-missing-version markers "
        "but the dependency name could not be extracted; treating as fatal",
        context.invocation.subcommand,
        context.invocation.crate_name,
    )
    raise context.error_cls(context.failure)


def _format_missing_dependency_failure(
    failure: str,
    *,
    missing_name: str,
    reason: str,
    guidance: str,
) -> str:
    """Return the shared fatal message shape for dependency index misses."""
    return f"{failure}; dependency {missing_name!r} {reason}. {guidance}"


def _log_missing_dependency_failure(
    logger: logging.Logger,
    invocation: _CargoInvocation,
    *,
    missing_name: str,
    detail: str,
) -> None:
    """Emit the shared warning shape for fatal dependency index misses."""
    logger.warning(
        "cargo %s for crate %s failed due to unindexed dependency %r %s",
        invocation.subcommand,
        invocation.crate_name,
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
        context.failure,
        missing_name=missing_name,
        reason=(
            "is not part of the current publish plan, so the unpublished "
            "workspace dependency override cannot help"
        ),
        guidance="Publish or index the dependency first.",
    )
    _log_missing_dependency_failure(
        context.logger,
        context.invocation,
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
        context.failure,
        missing_name=missing_name,
        reason=(
            f"appears after crate {context.invocation.crate_name!r} in publish order, "
            "so it will not be available when this crate is published"
        ),
        guidance=(
            "Adjust publish.order so the dependency comes first, or omit "
            "publish.order and rely on dependency-derived topological sorting."
        ),
    )
    _log_missing_dependency_failure(
        context.logger,
        context.invocation,
        missing_name=missing_name,
        detail=(
            "which appears after the current crate in publish order; cannot continue"
        ),
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
        context.failure,
        missing_name=missing_name,
        reason="is scheduled in this publish run but is not yet on crates.io",
        guidance=(
            "Enable the dry-run unpublished workspace dependency override, or "
            "follow the staged-publish workaround."
        ),
    )
    _log_missing_dependency_failure(
        context.logger,
        context.invocation,
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


def _publishable_name_indexes(plan: PublishPlan) -> dict[str, int]:
    """Return canonical publishable crate names keyed to publish-order indexes."""
    return {
        _canonical_crate_name(entry.name): index
        for index, entry in enumerate(plan.publishable)
    }


def _handle_index_missing_version(
    invocation: _CargoInvocation,
    *,
    handling: _IndexMissingVersionHandling,
    error_cls: type[Exception],
) -> None:
    """Handle a cargo failure caused by an unindexed sibling dependency.

    Raises ``error_cls`` (chosen by the caller) unless the missing dependency
    is in the current publish plan and the caller opted into the dry-run
    override.
    """
    exit_code, stdout, stderr = invocation.output
    failure = _format_cargo_failure_message(
        invocation.subcommand, invocation.crate_name, exit_code, (stdout, stderr)
    )
    context = _IndexMissingVersionFailure(
        error_cls=error_cls,
        invocation=invocation,
        failure=failure,
        logger=handling.logger,
    )

    missing_name = _extract_missing_dependency_name(stdout, stderr)
    if missing_name is None:
        _raise_name_extraction_failure(context)

    publishable_name_indexes = _publishable_name_indexes(handling.plan)
    canonical_current = _canonical_crate_name(invocation.crate_name)
    current_index = publishable_name_indexes.get(canonical_current)
    if current_index is None:
        message = (
            f"cargo {invocation.subcommand} failed for crate "
            f"{invocation.crate_name}, but that crate is not part of the "
            "current publish plan."
        )
        raise RuntimeError(message)
    missing_canonical_name = _canonical_crate_name(missing_name)
    missing_index = publishable_name_indexes.get(missing_canonical_name)
    if missing_index is None:
        _raise_out_of_plan_dependency(context, missing_name=missing_name)
    if missing_index >= current_index:
        _raise_out_of_order_dependency(context, missing_name=missing_name)

    if not handling.options.allow_unpublished_workspace_deps:
        _raise_unpublished_dependency_override_required(
            context, missing_name=missing_name
        )

    handling.logger.warning(
        "cargo %s for crate %s could not resolve sibling dependency %s "
        "from crates.io; continuing because the unpublished workspace "
        "dependency override is enabled",
        invocation.subcommand,
        invocation.crate_name,
        missing_name,
    )
    handling.logger.info(
        "Downgraded cargo %s failure for crate %s because dependency %s is "
        "part of the publish plan and the unpublished workspace dependency "
        "override is enabled",
        invocation.subcommand,
        invocation.crate_name,
        missing_name,
    )
