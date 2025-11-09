"""Publication planning and formatting helpers."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from lading.workspace import WorkspaceDependencyCycleError

if typ.TYPE_CHECKING:  # pragma: no cover - typing helper
    from pathlib import Path

    from lading.config import LadingConfig, StripPatchesSetting
    from lading.workspace import WorkspaceCrate, WorkspaceGraph


class PublishPlanError(RuntimeError):
    """Raised when the publish plan cannot be constructed."""


@dc.dataclass(frozen=True, slots=True)
class PublishPlan:
    """Describe which crates should be published from a workspace."""

    workspace_root: Path
    publishable: tuple[WorkspaceCrate, ...]
    skipped_manifest: tuple[WorkspaceCrate, ...]
    skipped_configuration: tuple[WorkspaceCrate, ...]
    missing_configuration_exclusions: tuple[str, ...] = ()

    @property
    def publishable_names(self) -> tuple[str, ...]:
        """Return the names of crates scheduled for publication."""
        return tuple(crate.name for crate in self.publishable)


def _categorize_crates(
    workspace_crates: typ.Sequence[WorkspaceCrate],
    exclusion_set: set[str],
) -> tuple[list[WorkspaceCrate], list[WorkspaceCrate], list[WorkspaceCrate]]:
    """Split workspace crates into publishable and skipped categories."""
    publishable: list[WorkspaceCrate] = []
    skipped_manifest: list[WorkspaceCrate] = []
    skipped_configuration: list[WorkspaceCrate] = []

    for crate in workspace_crates:
        if not crate.publish:
            skipped_manifest.append(crate)
        elif crate.name in exclusion_set:
            skipped_configuration.append(crate)
        else:
            publishable.append(crate)

    return publishable, skipped_manifest, skipped_configuration


def _process_order_and_collect_errors(
    configured_order: typ.Sequence[str],
    publishable_by_name: dict[str, WorkspaceCrate],
) -> tuple[list[WorkspaceCrate], set[str], set[str], list[str]]:
    """Collect ordering results and validation state for ``configured_order``."""
    ordered_crates: list[WorkspaceCrate] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    unknown_names: list[str] = []

    for crate_name in configured_order:
        crate = publishable_by_name.get(crate_name)
        if crate is None:
            unknown_names.append(crate_name)
            continue
        if crate_name in seen:
            duplicates.add(crate_name)
        else:
            ordered_crates.append(crate)
            seen.add(crate_name)

    return ordered_crates, seen, duplicates, unknown_names


def _build_order_validation_messages(
    duplicates: typ.AbstractSet[str],
    unknown: typ.Sequence[str],
    missing: typ.Sequence[str],
) -> list[str]:
    """Render validation failure messages for publish order problems."""
    messages: list[str] = []
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        messages.append(f"Duplicate publish.order entries: {duplicate_list}")
    if unknown:
        unknown_list = ", ".join(sorted(unknown))
        messages.append(
            "publish.order references crates outside the publishable set: "
            f"{unknown_list}"
        )
    if missing:
        missing_list = ", ".join(missing)
        messages.append(f"publish.order omits publishable crate(s): {missing_list}")
    return messages


def _resolve_configured_order(
    publishable_by_name: dict[str, WorkspaceCrate],
    configured_order: typ.Sequence[str],
) -> tuple[WorkspaceCrate, ...]:
    """Validate and return crates ordered according to configuration."""
    publishable_names = set(publishable_by_name)
    (
        ordered_publishable_list,
        seen_names,
        duplicates,
        unknown,
    ) = _process_order_and_collect_errors(
        configured_order,
        publishable_by_name,
    )
    missing = sorted(name for name in publishable_names if name not in seen_names)
    messages = _build_order_validation_messages(duplicates, unknown, missing)
    if messages:
        raise PublishPlanError("; ".join(messages))
    return tuple(ordered_publishable_list)


def _resolve_topological_order(
    workspace: WorkspaceGraph, publishable_names: set[str]
) -> tuple[WorkspaceCrate, ...]:
    """Return publishable crates ordered by workspace dependencies."""
    try:
        publishable_crates = tuple(
            crate for crate in workspace.crates if crate.name in publishable_names
        )
        subgraph = workspace.__class__(
            workspace_root=workspace.workspace_root,
            crates=publishable_crates,
        )
        return subgraph.topologically_sorted_crates()
    except WorkspaceDependencyCycleError as exc:
        cycle_list = ", ".join(exc.cycle_nodes)
        message = "Cannot determine publish order due to dependency cycle"
        if cycle_list:
            message = f"{message} involving: {cycle_list}"
        raise PublishPlanError(message) from exc


def plan_publication(
    workspace: WorkspaceGraph,
    configuration: LadingConfig,
    *,
    workspace_root: Path | None = None,
) -> PublishPlan:
    """Return the :class:`PublishPlan` for ``workspace`` and ``configuration``."""
    root_path = workspace.workspace_root if workspace_root is None else workspace_root
    configured_exclusions = tuple(configuration.publish.exclude)
    exclusion_set = set(configured_exclusions)

    workspace_crates = workspace.crates
    publishable, skipped_manifest, skipped_configuration = _categorize_crates(
        workspace_crates,
        exclusion_set,
    )
    crate_names = {crate.name for crate in workspace_crates}

    missing_exclusions = tuple(
        sorted(name for name in configured_exclusions if name not in crate_names)
    )

    publishable_by_name = {crate.name: crate for crate in publishable}
    publishable_names = set(publishable_by_name)

    if configured_order := configuration.publish.order:
        ordered_publishable = _resolve_configured_order(
            publishable_by_name,
            configured_order,
        )
    else:
        ordered_publishable = _resolve_topological_order(
            workspace,
            publishable_names,
        )

    ordered_skipped_manifest = tuple(
        sorted(skipped_manifest, key=lambda crate: crate.name)
    )
    ordered_skipped_configuration = tuple(
        sorted(skipped_configuration, key=lambda crate: crate.name)
    )

    return PublishPlan(
        workspace_root=root_path,
        publishable=ordered_publishable,
        skipped_manifest=ordered_skipped_manifest,
        skipped_configuration=ordered_skipped_configuration,
        missing_configuration_exclusions=missing_exclusions,
    )


def _format_crates_section(
    lines: list[str],
    crates: tuple[WorkspaceCrate, ...],
    *,
    header: str,
    empty_message: str | None = None,
) -> None:
    """Append publishable crate details to ``lines``."""
    if crates:
        lines.append(header)
        lines.extend(f"- {crate.name} @ {crate.version}" for crate in crates)
    elif empty_message is not None:
        lines.append(empty_message)


def _append_section[T](
    lines: list[str],
    items: typ.Sequence[T],
    *,
    header: str,
    formatter: typ.Callable[[T], str] = str,
) -> None:
    """Append formatted ``items`` to ``lines`` when a section has content."""
    if items:
        lines.append(header)
        lines.extend(f"- {formatter(item)}" for item in items)


def _format_plan(plan: PublishPlan, *, strip_patches: StripPatchesSetting) -> str:
    """Render ``plan`` to a human-readable summary for CLI output."""
    lines = [
        f"Publish plan for {plan.workspace_root}",
        f"Strip patch strategy: {strip_patches}",
    ]

    _format_crates_section(
        lines,
        plan.publishable,
        header=f"Crates to publish ({len(plan.publishable)}):",
        empty_message="Crates to publish: none",
    )
    _append_section(
        lines,
        plan.skipped_manifest,
        header="Skipped (publish = false):",
        formatter=lambda crate: crate.name,
    )
    _append_section(
        lines,
        plan.skipped_configuration,
        header="Skipped via publish.exclude:",
        formatter=lambda crate: crate.name,
    )
    _append_section(
        lines,
        plan.missing_configuration_exclusions,
        header="Configured exclusions not found in workspace:",
    )

    return "\n".join(lines)


__all__ = [
    "PublishPlan",
    "PublishPlanError",
    "_append_section",
    "_format_crates_section",
    "_format_plan",
    "plan_publication",
]
