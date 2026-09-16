"""Find and remove staging directories earlier publishes left behind.

Before issue #269, `lading publish` never removed its staged workspace copy.
A host inventory on 2026-09-15 found 3,936 `lading-publish-*` directories
under one temporary directory. Publishes no longer leak, but the leftovers
already on disk need something to sweep them, and `rm -rf` over a glob is a
poor thing to put in a guide.

The scope is deliberately narrow, because the command deletes. It considers
only the immediate children of one directory, only those whose names begin
with :data:`~lading.commands.publish_staging.STAGING_PREFIX`, and only real
directories rather than symbolic links. Nothing is removed unless the caller
asks: the report is the default, and ``--remove`` names the exception.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import shutil
import tempfile
import typing as typ
from pathlib import Path

from lading.commands.publish_staging import STAGING_PREFIX

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class LeftoverTree:
    """One staging directory a previous publish left behind.

    Attributes
    ----------
    path : Path
        The directory itself.
    size_bytes : int
        What removing it would reclaim, summed over the files within.
    """

    path: Path
    size_bytes: int


@dc.dataclass(frozen=True, slots=True)
class CleanOptions:
    """Options for :func:`run`.

    Attributes
    ----------
    location : Path | None
        Directory to search. Defaults to the system temporary directory,
        which is where a publish stages unless told otherwise.
    remove : bool
        Whether to remove what was found. Defaults to :data:`False`, so the
        command reports and stops; removal is the caller's explicit request.
    """

    location: Path | None = None
    remove: bool = False


def _resolve_location(location: Path | None) -> Path:
    """Return the directory to search, defaulting to the staging location.

    Returns
    -------
    Path
        The resolved search directory.
    """
    if location is None:
        return Path(tempfile.gettempdir()).resolve(strict=False)
    return Path(location).expanduser().resolve(strict=False)


def _tree_size(path: Path) -> int:
    """Return the total size of the files under ``path``, in bytes.

    Files that vanish or cannot be read while walking are counted as zero
    rather than aborting the report: a stale entry is exactly what this
    command exists to clear.

    Returns
    -------
    int
        The summed size of every regular file in the tree.
    """
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:  # pragma: no cover - racing a concurrent removal
            continue
    return total


def _is_leftover(candidate: Path) -> bool:
    """Report whether ``candidate`` is a staging directory this may remove.

    Both conditions are things the command must not delete: a directory
    without the staging prefix belongs to someone else, and a symbolic link
    would lead the removal to whatever it points at rather than to a staged
    copy. Nesting is excluded by the caller iterating one level rather than
    recursing; there is no third check here, because a resolved-parent test
    would be unreachable once links are already refused, and an unreachable
    safety check is worse than none.

    Returns
    -------
    bool
        Whether the candidate is in scope.
    """
    if not candidate.name.startswith(STAGING_PREFIX):
        return False
    return candidate.is_dir() and not candidate.is_symlink()


def find_leftovers(location: Path | None = None) -> tuple[LeftoverTree, ...]:
    """Return the staging directories found directly under ``location``.

    Parameters
    ----------
    location : Path | None
        Directory to search. Defaults to the system temporary directory.

    Returns
    -------
    tuple of LeftoverTree
        What was found, in name order, each with the bytes it holds.
    """
    root = _resolve_location(location)
    if not root.is_dir():
        return ()
    found = (
        LeftoverTree(path=candidate, size_bytes=_tree_size(candidate))
        for candidate in sorted(root.iterdir())
        if _is_leftover(candidate)
    )
    return tuple(found)


#: Binary size units, smallest first. Anything past the last one keeps that
#: unit rather than growing the table: a staged workspace is tens of
#: gigabytes, so terabytes would be a different problem entirely.
_SIZE_UNITS: typ.Final = ("B", "KiB", "MiB", "GiB")

#: The step between consecutive entries in :data:`_SIZE_UNITS`.
_SIZE_STEP: typ.Final = 1024


def _format_size(size_bytes: int) -> str:
    """Return ``size_bytes`` in the largest unit that keeps it above one.

    Returns
    -------
    str
        A human-readable size such as ``'1.4 GiB'``.

    Examples
    --------
    >>> _format_size(0)
    '0 B'
    >>> _format_size(2048)
    '2.0 KiB'
    >>> _format_size(1536 * 1024 * 1024)
    '1.5 GiB'
    """
    size = float(size_bytes)
    for unit in _SIZE_UNITS[:-1]:
        if size < _SIZE_STEP:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= _SIZE_STEP
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


def _summarize(
    leftovers: cabc.Sequence[LeftoverTree], location: Path, *, removed: bool
) -> str:
    """Return the report for what was found, and what was done with it.

    Returns
    -------
    str
        The rendered summary.
    """
    if not leftovers:
        return f"No staging directories found under {location}"
    total = sum(leftover.size_bytes for leftover in leftovers)
    verb = "Removed" if removed else "Found"
    noun = "directory" if len(leftovers) == 1 else "directories"
    headline = (
        f"{verb} {len(leftovers)} staging {noun} under {location}, "
        f"{_format_size(total)}"
    )
    lines = [
        headline,
        *(
            f"  {leftover.path.name}  {_format_size(leftover.size_bytes)}"
            for leftover in leftovers
        ),
    ]
    if not removed:
        lines.append("Pass --remove to delete them.")
    return "\n".join(lines)


def run(*, options: CleanOptions | None = None) -> str:
    """Report, and optionally remove, leftover staging directories.

    Parameters
    ----------
    options : CleanOptions | None
        Where to search and whether to remove. Defaults to reporting on the
        system temporary directory.

    Returns
    -------
    str
        The rendered summary.
    """
    active_options = options if options is not None else CleanOptions()
    location = _resolve_location(active_options.location)
    leftovers = find_leftovers(location)
    if not active_options.remove:
        return _summarize(leftovers, location, removed=False)

    removed: list[LeftoverTree] = []
    for leftover in leftovers:
        LOGGER.info("Removing staging directory %s", leftover.path)
        try:
            shutil.rmtree(leftover.path)
        except OSError:
            LOGGER.exception("Could not remove %s; leaving it", leftover.path)
            continue
        removed.append(leftover)
    return _summarize(removed, location, removed=True)


__all__: typ.Final = [
    "CleanOptions",
    "LeftoverTree",
    "find_leftovers",
    "run",
]
