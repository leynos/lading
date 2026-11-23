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
_DEPENDENCY_TABLES: typ.Final[tuple[str, ...]] = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
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
    seen: set[tuple[str, ...]] = {()}
    frontier: list[tuple[str, ...]] = [()]
    for parts in member_parts:
        if parts not in seen:
            seen.add(parts)
            frontier.append(parts)
    while frontier:
        parts = frontier.pop()
        document = _parse_manifest(root, parts)
        for raw in _path_dependencies(document):
            resolved = _resolve_dependency(root, parts, raw)
            if resolved is None or resolved in seen:
                continue
            seen.add(resolved)
            frontier.append(resolved)


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
) -> cabc.Iterator[str]:
    """Yield each path dependency declared by one manifest."""
    yield from _table_paths(document, _DEPENDENCY_TABLES)
    targets = document.get("target")
    if isinstance(targets, cabc.Mapping):
        for target in targets.values():
            if isinstance(target, cabc.Mapping):
                yield from _table_paths(target, _DEPENDENCY_TABLES)
    workspace = document.get("workspace")
    if isinstance(workspace, cabc.Mapping):
        yield from _table_paths(workspace, ("dependencies",))


def _table_paths(
    mapping: cabc.Mapping[str, typ.Any],
    names: cabc.Iterable[str],
) -> cabc.Iterator[str]:
    """Yield ``path`` values from the named dependency tables."""
    for name in names:
        table = mapping.get(name)
        if not isinstance(table, cabc.Mapping):
            continue
        for specification in table.values():
            if not isinstance(specification, cabc.Mapping):
                continue
            path = specification.get("path")
            if isinstance(path, str):
                yield path


def _resolve_dependency(
    root: Path,
    base_parts: tuple[str, ...],
    raw: str,
) -> tuple[str, ...] | None:
    """Return a path dependency's in-workspace components, or ``None``."""
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        return _walk_components(root, (), relative.parts, raw)
    return _walk_components(root, base_parts, candidate.parts, raw)


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
