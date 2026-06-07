"""Handle cargo index-lookup failures during publish workflows.

This module keeps the downgrade logic for missing registry
versions separate from the publish command orchestration. ``publish.py``
imports these helpers while running ``cargo package`` and ``cargo publish`` so
both phases share the same index-missing-version failure formatting and
override handling.
"""

from __future__ import annotations

import collections
import logging
import typing as typ

if typ.TYPE_CHECKING:
    from lading.commands.cargo_output_adapter import CargoIndexLookupFailure
    from lading.commands.publish import _PublishExecutionOptions
    from lading.commands.publish_plan import PublishPlan

LOGGER = logging.getLogger(__name__)

_INDEX_MISSING_VERSION_DOWNGRADE_COUNTER: collections.Counter[tuple[str, str, str]] = (
    collections.Counter()
)


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
    error_cls: type[Exception],
    lookup_failure: CargoIndexLookupFailure,
    failure_message: str,
) -> typ.NoReturn:
    """Log and raise when the missing dependency name cannot be extracted."""
    LOGGER.warning(
        "cargo %s for crate %s matched index-missing-version markers "
        "but the dependency name could not be extracted; treating as fatal",
        lookup_failure.subcommand,
        lookup_failure.crate_name,
    )
    raise error_cls(failure_message)


def _raise_out_of_plan_dependency(
    error_cls: type[Exception],
    lookup_failure: CargoIndexLookupFailure,
    failure_message: str,
    missing_name: str,
) -> typ.NoReturn:
    """Log and raise when the unindexed dependency is outside the publish plan."""
    message = (
        f"{failure_message}; missing dependency {missing_name!r} is not part "
        "of the current publish plan, so --allow-unpublished-workspace-deps "
        "cannot help. Publish or index the dependency first."
    )
    LOGGER.warning(
        "cargo %s for crate %s failed due to unindexed dependency %r "
        "which is not in the current publish plan; cannot continue",
        lookup_failure.subcommand,
        lookup_failure.crate_name,
        missing_name,
    )
    raise error_cls(message)


def _raise_allow_unpublished_flag_required(
    error_cls: type[Exception],
    lookup_failure: CargoIndexLookupFailure,
    failure_message: str,
    missing_name: str,
) -> typ.NoReturn:
    """Log and raise when ``--allow-unpublished-workspace-deps`` is not set.

    Called when the missing dependency is part of the publish plan but the
    caller has not opted into the dry-run override that downgrades the failure
    to a warning.
    """
    message = (
        f"{failure_message}; dependency {missing_name!r} is scheduled in "
        "this publish run but is not yet on crates.io. Re-run with "
        "--allow-unpublished-workspace-deps (dry-run only) or follow the "
        "staged-publish workaround in the user guide."
    )
    LOGGER.warning(
        "cargo %s for crate %s failed due to unindexed sibling dependency %r "
        "(in plan); re-run with --allow-unpublished-workspace-deps to "
        "downgrade to a warning, or follow the staged-publish workaround",
        lookup_failure.subcommand,
        lookup_failure.crate_name,
        missing_name,
    )
    raise error_cls(message)


def _canonical_crate_name(name: str) -> str:
    """Return the canonical crate name by normalising hyphens to underscores.

    Cargo error diagnostics report crate names using hyphens (e.g.
    ``my-crate``), whereas ``Cargo.toml`` manifests typically record the same
    package under underscores (e.g. ``my_crate``). Normalising both sides of
    any membership comparison prevents false out-of-plan classifications that
    would block the downgrade override.
    """
    return name.replace("-", "_")


def _record_index_missing_version_downgrade(
    failure: CargoIndexLookupFailure, missing_name: str
) -> None:
    """Increment the downgrade counter for an index-missing-version failure."""
    _INDEX_MISSING_VERSION_DOWNGRADE_COUNTER[
        failure.subcommand, failure.crate_name, missing_name
    ] += 1


def _handle_index_missing_version(
    failure: CargoIndexLookupFailure,
    *,
    plan: PublishPlan,
    options: _PublishExecutionOptions,
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

    missing_name = failure.missing_dependency_name
    if missing_name is None:
        _raise_name_extraction_failure(error_cls, failure, failure_message)

    publishable_names = {
        _canonical_crate_name(entry.name) for entry in plan.publishable
    }
    if _canonical_crate_name(missing_name) not in publishable_names:
        _raise_out_of_plan_dependency(error_cls, failure, failure_message, missing_name)

    if not options.allow_unpublished_workspace_deps:
        _raise_allow_unpublished_flag_required(
            error_cls, failure, failure_message, missing_name
        )

    _record_index_missing_version_downgrade(failure, missing_name)
    LOGGER.warning(
        "cargo %s for crate %s could not resolve sibling dependency %s "
        "from crates.io; continuing because "
        "--allow-unpublished-workspace-deps is set",
        failure.subcommand,
        failure.crate_name,
        missing_name,
    )
    LOGGER.info(
        "Downgraded cargo %s failure for crate %s because dependency %s is "
        "part of the publish plan and --allow-unpublished-workspace-deps is set",
        failure.subcommand,
        failure.crate_name,
        missing_name,
    )
