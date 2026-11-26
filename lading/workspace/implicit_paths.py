"""Resolve path-dependency destinations beneath a confined workspace root.

These helpers walk dependency components with ``lstat``, reject symlinked
components, and follow the normalised path Cargo actually opens. They belong to
implicit-member discovery but stay separate from manifest parsing and the
frontier orchestration in :mod:`lading.workspace.implicit_members`.
"""

import collections.abc as cabc
import os
import stat
from pathlib import Path

from lading.utils.confinement import (
    BumpTargetExaminationError,
    UnsafeBumpTargetError,
    is_link_like,
)

__all__ = ["resolve_dependency"]


def resolve_dependency(
    root: Path,
    base_parts: tuple[str, ...],
    raw: str,
) -> tuple[str, ...] | None:
    """Return the in-workspace components of a path dependency, or ``None``.

    Two spellings matter. The declared spelling receives the component checks,
    and the absolute lexical destination is what Cargo actually opens:
    redundant steps such as ``a/../dep`` are removed before lookup, so a
    missing ``a`` must not hide ``dep``, and a path that leaves the root and
    returns to it is still an in-workspace member.

    Returns
    -------
    tuple[str, ...] | None
        The dependency's directory components below ``root``, or ``None`` when
        it lies outside the workspace or names something that is not a
        directory.
    """
    candidate = Path(raw)
    if candidate.is_absolute():
        absolute = candidate
        declared = None
    else:
        absolute = root.joinpath(*base_parts, candidate)
        declared = _walk_components(root, base_parts, candidate.parts, raw)
    effective = _effective_components(root, absolute, raw)
    return effective or declared


def _effective_components(
    root: Path,
    absolute: Path,
    raw: str,
) -> tuple[str, ...] | None:
    """Return the components Cargo looks up after lexical normalization."""
    normalised = Path(os.path.normpath(absolute))
    try:
        relative = normalised.relative_to(root)
    except ValueError:
        return None
    return _walk_components(root, (), relative.parts, raw)


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
