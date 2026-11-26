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
import typing as typ
from pathlib import Path

from tomlkit import parse
from tomlkit.exceptions import TOMLKitError

from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    confine_relative_path,
)
from lading.workspace.implicit_paths import resolve_dependency

__all__ = [
    "read_manifest_text",
    "require_workspace_pointer",
    "validate_implicit_member_manifests",
]

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
    root_document: cabc.Mapping[str, typ.Any] | None = None
    while frontier:
        parts = frontier.pop()
        document = _parse_manifest(root, parts)
        require_workspace_pointer(root, parts, document)
        if not parts:
            root_document = document
        discovery.inherited.update(_inherited_names(document))
        frontier.extend(discovery.dependency_parts(parts, document))
    if root_document is None:  # pragma: no cover - the root is always queued
        root_document = _parse_manifest(root, ())
    _finish_workspace_dependencies(discovery, root_document)


def _finish_workspace_dependencies(
    discovery: _Discovery,
    root_document: cabc.Mapping[str, typ.Any],
) -> None:
    """Follow workspace dependencies inherited after the root was read.

    An implicit member discovered late can inherit a workspace dependency that
    was not known when the root's table was first scanned. Re-resolve the
    table until no newly inherited path remains.
    """
    while True:
        frontier = discovery.workspace_dependency_parts(root_document)
        if not frontier:
            return
        while frontier:
            parts = frontier.pop()
            document = _parse_manifest(discovery.root, parts)
            require_workspace_pointer(discovery.root, parts, document)
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
            self._enqueue(raw, parts, discovered)
        return discovered

    def workspace_dependency_parts(
        self,
        root_document: cabc.Mapping[str, typ.Any],
    ) -> list[tuple[str, ...]]:
        """Return newly inherited workspace dependency directories."""
        discovered: list[tuple[str, ...]] = []
        workspace = root_document.get("workspace")
        if isinstance(workspace, cabc.Mapping):
            for raw in _workspace_paths(workspace, self.inherited):
                self._enqueue(raw, (), discovered)
        return discovered

    def _enqueue(
        self,
        raw: str,
        base_parts: tuple[str, ...],
        discovered: list[tuple[str, ...]],
    ) -> None:
        """Record one dependency path that has not been scheduled yet."""
        resolved = resolve_dependency(self.root, base_parts, raw)
        if resolved is None or resolved in self.seen:
            return
        self.seen.add(resolved)
        discovered.append(resolved)


def require_workspace_pointer(
    root: Path,
    base_parts: tuple[str, ...],
    document: cabc.Mapping[str, typ.Any],
) -> None:
    """Reject a ``package.workspace`` pointer that names a different root.

    Cargo follows the pointer and reads the target workspace's manifests, so
    the requested root must already be that workspace. Rejecting the pointer
    before the metadata call keeps Cargo from opening an unvalidated file.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    base_parts : tuple[str, ...]
        Directory of the manifest that declares the pointer, relative to
        ``root``.
    document : cabc.Mapping[str, typ.Any]
        Parsed manifest document.

    Raises
    ------
    BumpPreflightError
        If the pointer names a root other than ``root``.
    """
    package = document.get("package")
    if not isinstance(package, cabc.Mapping):
        return
    pointer = package.get("workspace")
    if not isinstance(pointer, str):
        return
    target = Path(pointer)
    if not target.is_absolute():
        target = root.joinpath(*base_parts, target)
    normalised = Path(os.path.normpath(target))
    if normalised != root:
        message = (
            f"workspace manifest points at {normalised}, not {root}; pass "
            f"--workspace-root {normalised} so the requested root is the "
            "workspace Cargo reports"
        )
        raise BumpPreflightError(message)


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
