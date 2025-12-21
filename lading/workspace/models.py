"""Workspace graph models and builders for :mod:`lading`."""

from __future__ import annotations

import heapq
import typing as typ
from collections import abc as cabc
from collections import defaultdict
from pathlib import Path

import msgspec
from tomlkit import parse
from tomlkit.exceptions import TOMLKitError

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


class WorkspaceModelError(RuntimeError):
    """Raised when the workspace model cannot be constructed."""


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
                sorted(
                    {
                        dependency.name
                        for dependency in crate.dependencies
                        if _is_ordering_dependency(dependency, crates_by_name)
                    }
                )
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


def load_workspace(
    workspace_root: Path | str | None = None,
) -> WorkspaceGraph:
    """Return a :class:`WorkspaceGraph` constructed from ``cargo metadata``."""
    from lading.workspace.metadata import load_cargo_metadata

    metadata = load_cargo_metadata(workspace_root)
    return build_workspace_graph(metadata)


def build_workspace_graph(
    metadata: cabc.Mapping[str, typ.Any],
) -> WorkspaceGraph:
    """Convert ``cargo metadata`` output into :class:`WorkspaceGraph`."""
    try:
        workspace_root_value = metadata["workspace_root"]
    except KeyError as exc:
        raise WorkspaceModelError(WORKSPACE_ROOT_MISSING_MSG) from exc
    workspace_root = _normalise_workspace_root(workspace_root_value)
    packages = _expect_sequence(metadata.get("packages"), "packages")
    workspace_member_ids = tuple(
        _expect_string(member, "workspace_members[]")
        for member in _expect_sequence(
            metadata.get("workspace_members"), "workspace_members"
        )
    )
    package_lookup = _index_workspace_packages(packages, workspace_member_ids)
    crates: list[WorkspaceCrate] = []
    workspace_member_set = set(workspace_member_ids)
    for member_id in workspace_member_ids:
        raw_package = package_lookup.get(member_id)
        if raw_package is None:
            message = f"workspace member {member_id!r} missing from package list"
            raise WorkspaceModelError(message)
        crates.append(
            _build_crate(
                raw_package,
                package_lookup,
                workspace_member_set,
            )
        )
    return WorkspaceGraph(workspace_root=workspace_root, crates=tuple(crates))


def _index_workspace_packages(
    packages: cabc.Sequence[object],
    workspace_member_ids: cabc.Sequence[str],
) -> dict[str, cabc.Mapping[str, typ.Any]]:
    """Return mapping of workspace member IDs to package metadata."""
    member_set = set(workspace_member_ids)
    index: dict[str, cabc.Mapping[str, typ.Any]] = {}
    for package in packages:
        package_mapping = _expect_mapping(package, "packages[]")
        package_id = _expect_string(package_mapping.get("id"), "packages[].id")
        if package_id not in member_set:
            continue
        index[package_id] = package_mapping
    return index


def _build_crate(
    package: cabc.Mapping[str, typ.Any],
    package_lookup: cabc.Mapping[str, cabc.Mapping[str, typ.Any]],
    workspace_member_ids: set[str],
) -> WorkspaceCrate:
    """Construct a :class:`WorkspaceCrate` from ``cargo metadata`` package data."""
    package_id = _expect_string(package.get("id"), "packages[].id")
    name = _expect_string(package.get("name"), f"package {package_id!r} name")
    version = _expect_string(package.get("version"), f"package {package_id!r} version")
    manifest_path = _normalise_manifest_path(
        package.get("manifest_path"), f"package {package_id!r} manifest_path"
    )
    dependencies = _build_dependencies(package, package_lookup, workspace_member_ids)
    publish = _coerce_publish_setting(package.get("publish"), package_id)
    readme_is_workspace = _manifest_uses_workspace_readme(manifest_path)
    root_path = manifest_path.parent
    return WorkspaceCrate(
        id=package_id,
        name=name,
        version=version,
        manifest_path=manifest_path,
        root_path=root_path,
        publish=publish,
        readme_is_workspace=readme_is_workspace,
        dependencies=dependencies,
    )


def _build_dependencies(
    package: cabc.Mapping[str, typ.Any],
    package_lookup: cabc.Mapping[str, cabc.Mapping[str, typ.Any]],
    workspace_member_ids: set[str],
) -> tuple[WorkspaceDependency, ...]:
    """Return dependencies that reference other workspace members."""
    raw_dependencies = _expect_sequence(
        package.get("dependencies"),
        f"package {package.get('id')!r} dependencies",
        allow_none=True,
    )
    if raw_dependencies is None:
        return ()
    return tuple(
        dependency
        for dependency in (
            _as_workspace_dependency(entry, package_lookup, workspace_member_ids)
            for entry in raw_dependencies
        )
        if dependency is not None
    )


def _validate_dependency_mapping(
    entry: cabc.Mapping[str, typ.Any] | object,
) -> cabc.Mapping[str, typ.Any]:
    """Return ``entry`` as a mapping or raise if it is not."""
    if not isinstance(entry, cabc.Mapping):
        message = "dependency entries must be mappings"
        raise WorkspaceModelError(message)
    return typ.cast("cabc.Mapping[str, typ.Any]", entry)


def _lookup_workspace_target(
    entry: cabc.Mapping[str, typ.Any],
    package_lookup: cabc.Mapping[str, cabc.Mapping[str, typ.Any]],
    workspace_member_ids: set[str],
) -> tuple[str, str] | None:
    """Return the dependency target id and name when in the workspace."""
    target_id = entry.get("package")
    if not isinstance(target_id, str) or target_id not in workspace_member_ids:
        return None
    target_package = package_lookup.get(target_id)
    if target_package is None:
        return None
    target_name = _expect_string(
        target_package.get("name"), f"package {target_id!r} name"
    )
    return target_id, target_name


def _validate_dependency_kind(
    entry: cabc.Mapping[str, typ.Any],
) -> typ.Literal["normal", "dev", "build"] | None:
    """Return a validated dependency kind literal when present."""
    kind_value = entry.get("kind")
    if kind_value is None:
        return None
    if not isinstance(kind_value, str):
        message = (
            f"dependency kind must be string; received {type(kind_value).__name__}"
        )
        raise WorkspaceModelError(message)
    if kind_value not in ALLOWED_DEP_KINDS:
        message = f"unsupported dependency kind {kind_value!r}"
        raise WorkspaceModelError(message)
    return typ.cast("typ.Literal['normal', 'dev', 'build']", kind_value)


def _as_workspace_dependency(
    entry: cabc.Mapping[str, typ.Any] | object,
    package_lookup: cabc.Mapping[str, cabc.Mapping[str, typ.Any]],
    workspace_member_ids: set[str],
) -> WorkspaceDependency | None:
    """Convert ``entry`` into a :class:`WorkspaceDependency` when possible."""
    dependency = _validate_dependency_mapping(entry)
    target = _lookup_workspace_target(dependency, package_lookup, workspace_member_ids)
    if target is None:
        return None
    target_id, target_name = target
    manifest_name = _expect_string(
        dependency.get("name"),
        f"dependency {target_id!r} name",
    )
    kind_literal = _validate_dependency_kind(dependency)
    return WorkspaceDependency(
        package_id=target_id,
        name=target_name,
        manifest_name=manifest_name,
        kind=kind_literal,
    )


def _normalise_workspace_root(value: object) -> Path:
    """Return ``value`` as an absolute workspace root path."""
    if not isinstance(value, str | Path):
        message = (
            f"workspace_root must be a path string; received {type(value).__name__}"
        )
        raise WorkspaceModelError(message)
    from lading.utils.path import normalise_workspace_root

    return normalise_workspace_root(value)


def _normalise_manifest_path(value: object, field_name: str) -> Path:
    """Return ``value`` as an absolute :class:`Path` to a manifest."""
    if not isinstance(value, str | Path):
        message = f"{field_name} must be a path string; received {type(value).__name__}"
        raise WorkspaceModelError(message)
    path_value = Path(value).expanduser()
    return path_value.resolve(strict=False)


def _expect_mapping(value: object, field_name: str) -> cabc.Mapping[str, typ.Any]:
    """Return ``value`` as a string-keyed mapping or raise an error."""
    if isinstance(value, cabc.Mapping):
        return typ.cast("cabc.Mapping[str, typ.Any]", value)
    message = f"{field_name} must be a mapping; received {type(value).__name__}"
    raise WorkspaceModelError(message)


@typ.overload
def _expect_sequence(
    value: object,
    field_name: str,
    *,
    allow_none: typ.Literal[False] = False,
) -> cabc.Sequence[object]: ...


@typ.overload
def _expect_sequence(
    value: object,
    field_name: str,
    *,
    allow_none: typ.Literal[True],
) -> cabc.Sequence[object] | None: ...


def _expect_sequence(
    value: object,
    field_name: str,
    *,
    allow_none: bool = False,
) -> cabc.Sequence[object] | None:
    """Ensure ``value`` is a sequence (optionally ``None``)."""
    if value is None:
        if allow_none:
            return None
        message = f"{field_name} must be a sequence"
        raise WorkspaceModelError(message)
    if isinstance(value, cabc.Sequence) and not isinstance(
        value, str | bytes | bytearray
    ):
        return value
    message = f"{field_name} must be a sequence; received {type(value).__name__}"
    raise WorkspaceModelError(message)


def _expect_string(value: object, field_name: str) -> str:
    """Return ``value`` when it is a string, otherwise raise an error."""
    if isinstance(value, str):
        return value
    message = f"{field_name} must be a string; received {type(value).__name__}"
    raise WorkspaceModelError(message)


def _is_non_empty_sequence(value: object) -> bool:
    """Return ``True`` when ``value`` is a non-string sequence with content."""
    if not isinstance(value, cabc.Sequence):
        return False
    if isinstance(value, str | bytes | bytearray):
        return False
    return bool(value)


def _coerce_publish_setting(value: object, package_id: str) -> bool:
    """Return whether ``package_id`` should be considered publishable."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, cabc.Sequence) and not isinstance(
        value, str | bytes | bytearray
    ):
        return _is_non_empty_sequence(value)
    message = (
        f"publish setting for package {package_id!r} must be false, a list, or null"
    )
    raise WorkspaceModelError(message)


def _extract_readme_workspace_flag(package_table: object) -> bool:
    """Return ``True`` when ``package_table`` opts into workspace readme."""
    if not isinstance(package_table, cabc.Mapping):
        return False
    package_mapping = typ.cast("cabc.Mapping[str, typ.Any]", package_table)
    readme_value = package_mapping.get("readme")
    if not isinstance(readme_value, cabc.Mapping):
        return False
    readme_mapping = typ.cast("cabc.Mapping[str, typ.Any]", readme_value)
    workspace_flag = readme_mapping.get("workspace")
    return bool(workspace_flag)


def _manifest_uses_workspace_readme(manifest_path: Path) -> bool:
    """Return ``True`` when ``readme.workspace`` is set in ``manifest_path``."""
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        message = f"manifest not found: {manifest_path}"
        raise WorkspaceModelError(message) from exc
    try:
        document = parse(text)
    except TOMLKitError as exc:
        message = f"failed to parse manifest {manifest_path}: {exc}"
        raise WorkspaceModelError(message) from exc
    package_table = document.get("package")
    return _extract_readme_workspace_flag(package_table)
