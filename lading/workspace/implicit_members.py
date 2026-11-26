"""Validate implicit workspace-member manifests before Cargo reads them.

Cargo treats a path dependency inside the workspace directory as an implicit
workspace member and reads its manifest while discovering the member set. The
declared-member checks in :mod:`lading.workspace.confined_discovery` therefore
cannot see those manifests, and a special file among them could block
``cargo metadata`` before any rejection runs.

This module reproduces Cargo's path-dependency discovery lexically. Every
component is inspected with ``lstat``, symlinked components are rejected, and a
manifest is read only after it has been classified as a regular file. Path
dependencies outside the workspace are not members and are left to Cargo.
Excluded directories are still inspected: Cargo may read a path dependency's
manifest even when the directory is not a member, and refusing an unreadable
one is safer than blocking on it.
"""

import collections.abc as cabc
import dataclasses as dc
import os
import stat
import typing as typ
from pathlib import Path

from tomlkit import parse
from tomlkit.exceptions import TOMLKitError

from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    UnsafeBumpTargetError,
    confine_relative_path,
    is_link_like,
)

__all__ = ["read_manifest_text", "validate_implicit_member_manifests"]

_ROOT_MANIFEST = "Cargo.toml"
_DEPENDENCY_TABLES: typ.Final[tuple[tuple[str, ...], ...]] = (
    ("dependencies",),
    ("dev-dependencies", "dev_dependencies"),
    ("build-dependencies", "build_dependencies"),
)


def read_manifest_text(path: Path, *, key: str) -> str:
    """Read ``path`` as UTF-8 text, reporting read and decode failures.

    Parameters
    ----------
    path : Path
        Manifest path that has already been proved to be a regular file.
    key : str
        Configuration key or role naming the manifest in diagnostics.

    Returns
    -------
    str
        The manifest text.

    Raises
    ------
    BumpTargetExaminationError
        If the file cannot be read or decoded as UTF-8.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        message = f"{key}: cannot read {path}: {exc}"
        raise BumpTargetExaminationError(message) from exc


def validate_implicit_member_manifests(
    root: Path,
    member_parts: cabc.Iterable[tuple[str, ...]],
) -> None:
    """Validate every manifest Cargo may read as an implicit member.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    member_parts : cabc.Iterable[tuple[str, ...]]
        Declared member directory components. The workspace root manifest is
        always inspected, because ``[workspace.dependencies]`` path entries are
        resolved relative to it.

    Raises
    ------
    BumpPreflightError
        If a manifest is not valid TOML.
    BumpTargetExaminationError
        If a component or manifest cannot be examined or read.
    MissingBumpTargetError
        If a path-dependency directory has no manifest.
    UnsafeBumpTargetError
        If a manifest is not a regular file or a dependency path traverses a
        symlinked component.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by helpers
    frontier = _initial_frontier(member_parts)
    discovery = _Discovery(
        root=root,
        seen=set(frontier),
        inherited=set(),
    )
    while frontier:
        parts = frontier.pop()
        document = _parse_manifest(root, parts)
        discovery.inherited.update(_inherited_names(document))
        frontier.extend(discovery.dependency_parts(parts, document))


def _initial_frontier(
    member_parts: cabc.Iterable[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """Return the root manifest and each distinct declared member."""
    frontier: list[tuple[str, ...]] = [()]
    for parts in member_parts:
        if parts not in frontier:
            frontier.append(parts)
    return frontier


@dc.dataclass(slots=True)
class _Discovery:
    """State threaded through implicit-member discovery."""

    root: Path
    seen: set[tuple[str, ...]]
    inherited: set[str]

    def dependency_parts(
        self,
        parts: tuple[str, ...],
        document: cabc.Mapping[str, typ.Any],
    ) -> list[tuple[str, ...]]:
        """Return newly discovered in-workspace dependency directories."""
        discovered: list[tuple[str, ...]] = []
        for raw in _path_dependencies(document, inherited=self.inherited):
            resolved = _resolve_dependency(self.root, parts, raw)
            if resolved is None or resolved in self.seen:
                continue
            self.seen.add(resolved)
            discovered.append(resolved)
        return discovered


def _parse_manifest(
    root: Path,
    parts: tuple[str, ...],
) -> cabc.Mapping[str, typ.Any]:
    """Return the parsed manifest at ``parts``, after a confined read."""
    pattern = "/".join((*parts, _ROOT_MANIFEST))
    key = "workspace manifest" if not parts else f"workspace member manifest {pattern}"
    confined = confine_relative_path(root, pattern, key=key)
    text = read_manifest_text(confined.path, key=key)
    try:
        return parse(text)
    except TOMLKitError as exc:
        message = f"{key}: not valid TOML: {confined.display}: {exc}"
        raise BumpPreflightError(message) from exc


def _path_dependencies(
    document: cabc.Mapping[str, typ.Any],
    *,
    inherited: cabc.Set[str],
) -> cabc.Iterator[str]:
    """Yield each path dependency the manifest's package actually declares.

    A ``[workspace.dependencies]`` entry only becomes a member when the package
    inherits it with ``workspace = true``; an unused entry never makes Cargo
    open the target manifest, so it is not followed.

    Yields
    ------
    str
        Each declared dependency path, in declaration order.
    """
    yield from _table_paths(document, _DEPENDENCY_TABLES)
    for target in _target_tables(document):
        yield from _table_paths(target, _DEPENDENCY_TABLES)
    workspace = document.get("workspace")
    if isinstance(workspace, cabc.Mapping):
        yield from _workspace_paths(workspace, inherited)


def _inherited_names(
    document: cabc.Mapping[str, typ.Any],
) -> frozenset[str]:
    """Return the ``[workspace.dependencies]`` names this package inherits."""
    names: set[str] = set()
    tables = [document, *_target_tables(document)]
    for table in tables:
        for spellings in _DEPENDENCY_TABLES:
            dependencies = _first_table(table, spellings)
            if dependencies is not None:
                names.update(_inheriting_names(dependencies))
    return frozenset(names)


def _target_tables(
    document: cabc.Mapping[str, typ.Any],
) -> cabc.Iterator[cabc.Mapping[str, typ.Any]]:
    """Yield each ``[target.'cfg(...)']`` mapping."""
    targets = document.get("target")
    if isinstance(targets, cabc.Mapping):
        for target in targets.values():
            if isinstance(target, cabc.Mapping):
                yield typ.cast("cabc.Mapping[str, typ.Any]", target)


def _inheriting_names(
    table: cabc.Mapping[str, typ.Any],
) -> cabc.Iterator[str]:
    """Yield dependency names declared with ``workspace = true``."""
    for name, specification in table.items():
        if (
            isinstance(specification, cabc.Mapping)
            and specification.get("workspace") is True
        ):
            yield name


def _workspace_paths(
    workspace: cabc.Mapping[str, typ.Any],
    inherited: cabc.Set[str],
) -> cabc.Iterator[str]:
    """Yield the paths of inherited ``[workspace.dependencies]`` entries."""
    table = workspace.get("dependencies")
    if not isinstance(table, cabc.Mapping):
        return
    for name, specification in table.items():
        if name not in inherited:
            continue
        path = _specification_path(specification)
        if path is not None:
            yield path


def _table_paths(
    mapping: cabc.Mapping[str, typ.Any],
    names: cabc.Iterable[tuple[str, ...]],
) -> cabc.Iterator[str]:
    """Yield ``path`` values from the first declared spelling of each table."""
    for spellings in names:
        table = _first_table(mapping, spellings)
        if table is not None:
            yield from _specification_paths(table)


def _first_table(
    mapping: cabc.Mapping[str, typ.Any],
    spellings: cabc.Iterable[str],
) -> cabc.Mapping[str, typ.Any] | None:
    """Return the first table present under any accepted spelling."""
    for name in spellings:
        table = mapping.get(name)
        if isinstance(table, cabc.Mapping):
            return typ.cast("cabc.Mapping[str, typ.Any]", table)
    return None


def _specification_paths(
    table: cabc.Mapping[str, typ.Any],
) -> cabc.Iterator[str]:
    """Yield the path of every table-valued dependency specification."""
    return (
        path for path in map(_specification_path, table.values()) if path is not None
    )


def _specification_path(specification: object) -> str | None:
    """Return a dependency specification's path, if it declares one."""
    if not isinstance(specification, cabc.Mapping):
        return None
    path = specification.get("path")
    return path if isinstance(path, str) else None


def _resolve_dependency(
    root: Path,
    base_parts: tuple[str, ...],
    raw: str,
) -> tuple[str, ...] | None:
    """Return the in-workspace components of a path dependency, or ``None``.

    Two spellings matter. The declared spelling receives the component checks,
    and the normalised path is what Cargo actually opens: redundant steps such
    as ``a/../dep`` are removed before lookup, so a missing ``a`` must not hide
    ``dep`` from the preflight.

    Returns
    -------
    tuple[str, ...] | None
        The dependency's directory components below ``root``, or ``None`` when
        it lies outside the workspace or names something that is not a
        directory.
    """
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        start: tuple[str, ...] = ()
        components = relative.parts
    else:
        start = base_parts
        components = candidate.parts
    declared = _walk_components(root, start, components, raw)
    effective = _normalised_parts(start, components)
    if effective is None:
        return declared
    return _walk_components(root, (), effective, raw) or declared


def _normalised_parts(
    base_parts: tuple[str, ...],
    components: cabc.Sequence[str],
) -> tuple[str, ...] | None:
    """Return the components Cargo looks up after removing redundant steps."""
    parts = list(base_parts)
    for component in components:
        if component == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(component)
    return tuple(parts)


def _walk_components(
    root: Path,
    start_parts: tuple[str, ...],
    components: cabc.Iterable[str],
    raw: str,
) -> tuple[str, ...] | None:
    """Walk dependency components, rejecting links and workspace escapes."""
    parts = list(start_parts)
    # ``Path.parts`` never contains ``""`` or ``"."``, so no such component
    # needs handling here.
    for component in components:
        if component == "..":
            if not parts:
                return None
            parts.pop()
            continue
        status = _lstat_dependency(root.joinpath(*parts, component), raw)
        if status is None:
            return None
        if is_link_like(status):
            message = f"path dependency traverses a symlink: {raw}"
            raise UnsafeBumpTargetError(message)
        if not stat.S_ISDIR(status.st_mode):
            return None
        parts.append(component)
    return tuple(parts)


def _lstat_dependency(candidate: Path, raw: str) -> os.stat_result | None:
    """Return the ``lstat`` result, or ``None`` when the path is absent."""
    try:
        return os.lstat(candidate)
    except FileNotFoundError:
        return None
    except OSError as exc:
        message = f"cannot examine path dependency {raw}: {exc}"
        raise BumpTargetExaminationError(message) from exc
