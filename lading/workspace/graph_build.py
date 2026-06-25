"""Builders converting ``cargo metadata`` output into workspace models.

Extracted from :mod:`lading.workspace.models` (issue #108) so the data
structures and topology stay separate from the metadata-parsing layer. The
module shares the coercion bindings defined in ``models`` so all workspace
validation failures raise :class:`WorkspaceModelError` with the canonical
message shape from :mod:`lading.toml_coerce`.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

from tomlkit import parse
from tomlkit.exceptions import TOMLKitError

from lading.workspace.models import (
    ALLOWED_DEP_KINDS,
    WORKSPACE_ROOT_MISSING_MSG,
    WorkspaceCrate,
    WorkspaceDependency,
    WorkspaceGraph,
    WorkspaceIndex,
    WorkspaceModelError,
    _expect_mapping,
    _expect_sequence,
    _expect_string,
    _is_non_empty_sequence,
)


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
    packages = _expect_sequence(metadata.get("packages"), "packages", allow_none=False)
    workspace_members = _expect_sequence(
        metadata.get("workspace_members"), "workspace_members", allow_none=False
    )
    workspace_member_ids = tuple(
        _expect_string(member, "workspace_members[]") for member in workspace_members
    )
    package_lookup = _index_workspace_packages(packages, workspace_member_ids)
    workspace_index = _build_workspace_index(package_lookup)
    crates = _collect_workspace_crates(
        package_lookup=package_lookup,
        workspace_member_ids=workspace_member_ids,
        workspace_index=workspace_index,
    )
    return WorkspaceGraph(workspace_root=workspace_root, crates=crates)


def _collect_workspace_crates(
    package_lookup: dict[str, cabc.Mapping[str, typ.Any]],
    workspace_member_ids: cabc.Sequence[str],
    workspace_index: WorkspaceIndex,
) -> tuple[WorkspaceCrate, ...]:
    """Return a :class:`WorkspaceCrate` tuple for each workspace member ID."""
    crates: list[WorkspaceCrate] = []
    for member_id in workspace_member_ids:
        raw_package = package_lookup.get(member_id)
        if raw_package is None:
            message = f"workspace member {member_id!r} missing from package list"
            raise WorkspaceModelError(message)
        crates.append(_build_crate(raw_package, workspace_index))
    return tuple(crates)


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
    workspace_index: WorkspaceIndex,
) -> WorkspaceCrate:
    """Construct a :class:`WorkspaceCrate` from ``cargo metadata`` package data."""
    package_id = _expect_string(package.get("id"), "packages[].id")
    name = _expect_string(package.get("name"), f"package {package_id!r} name")
    version = _expect_string(package.get("version"), f"package {package_id!r} version")
    manifest_path = _normalise_manifest_path(
        package.get("manifest_path"), f"package {package_id!r} manifest_path"
    )
    dependencies = _build_dependencies(package, workspace_index)
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
    workspace_index: WorkspaceIndex,
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
            _as_workspace_dependency(entry, workspace_index)
            for entry in raw_dependencies
        )
        if dependency is not None
    )


def _build_workspace_index(
    package_lookup: cabc.Mapping[str, cabc.Mapping[str, typ.Any]],
) -> WorkspaceIndex:
    """Return workspace package lookups keyed by id and package name."""
    members_by_name: dict[str, str] = {}
    for package_id, package in package_lookup.items():
        package_name = _expect_string(
            package.get("name"), f"package {package_id!r} name"
        )
        existing_id = members_by_name.get(package_name)
        if existing_id is not None and existing_id != package_id:
            message = (
                f"workspace package name {package_name!r} maps to multiple ids: "
                f"{existing_id!r}, {package_id!r}"
            )
            raise WorkspaceModelError(message)
        members_by_name[package_name] = package_id
    return WorkspaceIndex(packages=package_lookup, members_by_name=members_by_name)


def _validate_dependency_mapping(
    entry: cabc.Mapping[str, typ.Any] | object,
) -> cabc.Mapping[str, typ.Any]:
    """Return ``entry`` as a mapping or raise if it is not."""
    if not isinstance(entry, cabc.Mapping):
        message = "dependency entries must be mappings"
        raise WorkspaceModelError(message)
    return typ.cast("cabc.Mapping[str, typ.Any]", entry)


def _validate_workspace_dependency_path(
    entry: cabc.Mapping[str, typ.Any],
    target_package: cabc.Mapping[str, typ.Any],
) -> bool:
    """Return whether an entry path matches the workspace dependency target."""
    dependency_path = entry.get("path")
    if dependency_path is None:
        return True
    if not isinstance(dependency_path, str):
        return False
    target_manifest_path = _normalise_manifest_path(
        target_package.get("manifest_path"),
        "dependency target manifest_path",
    )
    dependency_root = Path(dependency_path).expanduser().resolve(strict=False)
    return dependency_root == target_manifest_path.parent


def _lookup_workspace_target(
    entry: cabc.Mapping[str, typ.Any],
    workspace_index: WorkspaceIndex,
) -> tuple[str, str] | None:
    """Return the dependency target id and name when in the workspace."""
    # External sources should never resolve to workspace dependencies.
    if entry.get("source") is not None:
        return None
    for candidate_name in _dependency_candidate_names(entry):
        target_id = workspace_index.members_by_name.get(candidate_name)
        if target_id is None:
            continue
        target_package = workspace_index.packages.get(target_id)
        if target_package is None:
            continue
        if not _validate_workspace_dependency_path(entry, target_package):
            continue

        target_name = _expect_string(
            target_package.get("name"), f"package {target_id!r} name"
        )
        return target_id, target_name
    return None


def _dependency_candidate_names(entry: cabc.Mapping[str, typ.Any]) -> tuple[str, ...]:
    """Return candidate dependency package names from metadata."""
    names: list[str] = []
    dependency_name = entry.get("name")
    if isinstance(dependency_name, str):
        names.append(dependency_name)
    # Some metadata producers include `package` for canonical dependency names.
    package_name = entry.get("package")
    if isinstance(package_name, str) and package_name not in names:
        names.append(package_name)
    return tuple(names)


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
    workspace_index: WorkspaceIndex,
) -> WorkspaceDependency | None:
    """Convert ``entry`` into a :class:`WorkspaceDependency` when possible."""
    dependency = _validate_dependency_mapping(entry)
    target = _lookup_workspace_target(dependency, workspace_index)
    if target is None:
        return None
    target_id, target_name = target
    manifest_name = _dependency_manifest_name(dependency, target_id)
    kind_literal = _validate_dependency_kind(dependency)
    return WorkspaceDependency(
        package_id=target_id,
        name=target_name,
        manifest_name=manifest_name,
        kind=kind_literal,
    )


def _dependency_manifest_name(
    dependency: cabc.Mapping[str, typ.Any],
    target_id: str,
) -> str:
    """Return the dependency name used in manifests."""
    rename_value = dependency.get("rename")
    if isinstance(rename_value, str) and rename_value:
        return rename_value
    dependency_name = dependency.get("name")
    if isinstance(dependency_name, str):
        return dependency_name
    package_name = dependency.get("package")
    if isinstance(package_name, str):
        return package_name
    return _expect_string(
        dependency_name,
        f"dependency {target_id!r} name",
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
