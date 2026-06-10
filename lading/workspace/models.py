"""Workspace graph models and builders for :mod:`lading`."""

from __future__ import annotations

import collections.abc as cabc
import functools
import dataclasses as dc
import heapq
import typing as typ
from collections import defaultdict
from pathlib import Path

import msgspec
from lading import toml_coerce
from lading.exceptions import LadingError

WORKSPACE_ROOT_MISSING_MSG = "cargo metadata missing 'workspace_root'"

ALLOWED_DEP_KINDS: typ.Final[set[str]] = {"normal", "dev", "build"}
ORDER_DEPENDENCY_KINDS: typ.Final[set[str]] = {"normal", "build"}


def _is_ordering_dependency(
    dependency: WorkspaceDependency,
    crates_by_name: dict[str, WorkspaceCrate],
) -> bool:
    """Return ``True`` when ``dependency`` influences publish ordering."""
    if dependency.name not in crates_by_name:
        return False
    if dependency.kind is None:
        return True
    return dependency.kind in ORDER_DEPENDENCY_KINDS


class WorkspaceModelError(LadingError):
    """Raised when the workspace model cannot be constructed."""


# Coercion helpers bound to the workspace error type; the shared
# implementations live in lading.toml_coerce (issue #108).
_expect_mapping = functools.partial(
    toml_coerce.expect_mapping, error=WorkspaceModelError
)
_expect_sequence = functools.partial(
    toml_coerce.expect_sequence, error=WorkspaceModelError
)
_expect_string = functools.partial(
    toml_coerce.expect_string, error=WorkspaceModelError
)
_is_non_empty_sequence = toml_coerce.is_non_empty_sequence


class WorkspaceDependencyCycleError(WorkspaceModelError):
    """Raised when a dependency cycle prevents ordering workspace crates."""

    def __init__(self, cycle_nodes: cabc.Sequence[str]) -> None:
        """Initialise the cycle error with sorted node names."""
        self.cycle_nodes = tuple(sorted(cycle_nodes))
        message = "Workspace dependency graph contains a cycle"
        if self.cycle_nodes:
            joined = ", ".join(self.cycle_nodes)
            message = f"{message}: {joined}"
        super().__init__(message)


class WorkspaceDependency(msgspec.Struct, frozen=True, kw_only=True):
    """Represents a dependency between two workspace crates."""

    package_id: str
    name: str
    manifest_name: str
    kind: typ.Literal["normal", "dev", "build"] | None = None


class WorkspaceCrate(msgspec.Struct, frozen=True, kw_only=True):
    """Represents a single crate discovered in the workspace."""

    id: str
    name: str
    version: str
    manifest_path: Path
    root_path: Path
    publish: bool
    readme_is_workspace: bool
    dependencies: tuple[WorkspaceDependency, ...]


class WorkspaceGraph(msgspec.Struct, frozen=True, kw_only=True):
    """Represents the crates and relationships for a workspace."""

    workspace_root: Path
    crates: tuple[WorkspaceCrate, ...]

    def _build_dependency_graph(
        self,
        crates_by_name: dict[str, WorkspaceCrate],
    ) -> dict[str, tuple[str, ...]]:
        """Build a dependency map for workspace crates."""
        dependency_map: dict[str, tuple[str, ...]] = {}
        for crate in crates_by_name.values():
            dependency_names = tuple(
                sorted({
                    dependency.name
                    for dependency in crate.dependencies
                    if _is_ordering_dependency(dependency, crates_by_name)
                })
            )
            dependency_map[crate.name] = dependency_names
        return dependency_map

    def _initialize_topological_structures(
        self,
        dependency_map: dict[str, tuple[str, ...]],
    ) -> tuple[dict[str, int], defaultdict[str, set[str]]]:
        """Initialise incoming counts and dependents for topological sort."""
        incoming_counts: dict[str, int] = {}
        dependents: defaultdict[str, set[str]] = defaultdict(set)
        for name, dependencies in dependency_map.items():
            incoming_counts[name] = len(dependencies)
            for dependency_name in dependencies:
                dependents[dependency_name].add(name)
        for name in dependency_map:
            dependents.setdefault(name, set())
        return incoming_counts, dependents

    def _perform_kahn_sort(
        self,
        incoming_counts: dict[str, int],
        dependents: defaultdict[str, set[str]],
    ) -> list[str]:
        """Execute Kahn's algorithm to produce topological ordering."""
        available = [name for name, count in incoming_counts.items() if count == 0]
        heapq.heapify(available)
        ordered_names: list[str] = []

        while available:
            current = heapq.heappop(available)
            ordered_names.append(current)
            for dependent in dependents[current]:
                incoming_counts[dependent] -= 1
                if incoming_counts[dependent] == 0:
                    heapq.heappush(available, dependent)

        return ordered_names

    def _collect_cycle_nodes(
        self,
        crates_by_name: dict[str, WorkspaceCrate],
        ordered_names: list[str],
        incoming_counts: dict[str, int],
    ) -> list[str]:
        """Identify nodes involved in a dependency cycle."""
        cycle_nodes = [
            name
            for name, count in incoming_counts.items()
            if count > 0 and name not in ordered_names
        ]
        cycle_nodes.extend(
            name
            for name in crates_by_name
            if name not in ordered_names and name not in cycle_nodes
        )
        return cycle_nodes

    def topologically_sorted_crates(self) -> tuple[WorkspaceCrate, ...]:
        """Return ``self.crates`` ordered so dependencies precede dependents."""
        crates_by_name = {crate.name: crate for crate in self.crates}
        dependency_map = self._build_dependency_graph(crates_by_name)
        incoming_counts, dependents = self._initialize_topological_structures(
            dependency_map
        )
        ordered_names = self._perform_kahn_sort(incoming_counts, dependents)

        if len(ordered_names) != len(crates_by_name):
            cycle_nodes = self._collect_cycle_nodes(
                crates_by_name,
                ordered_names,
                incoming_counts,
            )
            raise WorkspaceDependencyCycleError(cycle_nodes)

        return tuple(crates_by_name[name] for name in ordered_names)

    @property
    def crates_by_name(self) -> dict[str, WorkspaceCrate]:
        """Return a name-indexed mapping of workspace crates."""
        return {crate.name: crate for crate in self.crates}


@dc.dataclass(frozen=True, slots=True)
class WorkspaceIndex:
    """Lookup index used to resolve workspace dependencies."""

    packages: cabc.Mapping[str, cabc.Mapping[str, typ.Any]]
    members_by_name: cabc.Mapping[str, str]


# Re-exports keep the public construction API stable while the builders live
# in graph_build (issue #108). Imported at the bottom to avoid a circular
# import: graph_build depends on the model types above.
from lading.workspace.graph_build import (  # noqa: E402
    build_workspace_graph,
    load_workspace,
)

__all__ = [
    "WorkspaceCrate",
    "WorkspaceDependency",
    "WorkspaceDependencyCycleError",
    "WorkspaceGraph",
    "WorkspaceIndex",
    "WorkspaceModelError",
    "build_workspace_graph",
    "load_workspace",
]

