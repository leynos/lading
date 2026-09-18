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

Scope alone says nothing about time, so a removal also takes the tree's claim
from :mod:`lading.commands.staging_lock` and holds it until the deletion is
done: a tree a running publish still holds is skipped and reported rather than
deleted, and one that is free cannot be claimed while it is going.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import shutil
import tempfile
import typing as typ
from pathlib import Path

from lading.commands import staging_lock
from lading.commands.publish_staging import STAGING_PREFIX
from lading.exceptions import LadingError

LOGGER = logging.getLogger(__name__)


class CleanError(LadingError):
    """A sweep that could not be carried out.

    Raised when the search directory itself cannot be listed, which is the
    one filesystem failure here that is not a stale entry to step over. The
    per-file errors met while sizing a tree are counted as zero and walked
    past, because a directory vanishing underfoot is exactly what this
    command exists to clear; a location that cannot be read is different in
    kind, and reporting it as an empty sweep would tell the caller their disk
    was clean when nothing had been looked at.
    """


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

    Raises
    ------
    CleanError
        If the search directory exists but cannot be listed.
    """
    root = _resolve_location(location)
    if not root.is_dir():
        return ()
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        message = f"Cannot read the staging location: {root}"
        raise CleanError(message) from exc
    found = (
        LeftoverTree(path=candidate, size_bytes=_tree_size(candidate))
        for candidate in entries
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


def _entries(leftovers: cabc.Sequence[LeftoverTree]) -> list[str]:
    """Return one indented line per tree, naming it and what it holds.

    Returns
    -------
    list of str
        The rendered entry lines, in the order given.
    """
    return [
        f"  {leftover.path.name}  {_format_size(leftover.size_bytes)}"
        for leftover in leftovers
    ]


def _total_size(leftovers: cabc.Sequence[LeftoverTree]) -> str:
    """Return the summed size of ``leftovers``, rendered for the report.

    Returns
    -------
    str
        A human-readable size.
    """
    return _format_size(sum(leftover.size_bytes for leftover in leftovers))


def _noun(count: int) -> str:
    """Return the noun agreeing with ``count``.

    Returns
    -------
    str
        Either ``'directory'`` or ``'directories'``.
    """
    return "directory" if count == 1 else "directories"


def _summarize(
    leftovers: cabc.Sequence[LeftoverTree],
    location: Path,
    *,
    removed: cabc.Sequence[LeftoverTree] | None = None,
    skipped: cabc.Sequence[LeftoverTree] = (),
) -> str:
    """Return the report for what was found, and what was done with it.

    ``removed`` is :data:`None` when the caller asked only for a report, and
    the trees actually removed otherwise. It is separate from ``leftovers``
    because a removal can fail: summarising ``removed`` alone would announce
    that nothing was found whenever every :func:`shutil.rmtree` raised, which
    is the opposite of what happened and would leave the caller believing the
    directory had been swept. ``skipped`` holds the trees a running publish
    still owns, which are neither a failure nor a removal and must not read
    as either.

    Returns
    -------
    str
        The rendered summary.
    """
    if not leftovers:
        return f"No staging directories found under {location}"
    if removed is None:
        headline = (
            f"Found {len(leftovers)} staging {_noun(len(leftovers))} under "
            f"{location}, {_total_size(leftovers)}"
        )
        return "\n".join([
            headline,
            *_entries(leftovers),
            "Pass --remove to delete them.",
        ])
    if len(removed) == len(leftovers):
        headline = (
            f"Removed {len(removed)} staging {_noun(len(removed))} under "
            f"{location}, {_total_size(removed)}"
        )
        return "\n".join([headline, *_entries(removed)])
    accounted = {leftover.path for leftover in (*removed, *skipped)}
    failed = [leftover for leftover in leftovers if leftover.path not in accounted]
    headline = (
        f"Removed {len(removed)} of {len(leftovers)} staging "
        f"{_noun(len(leftovers))} under {location}, {_total_size(removed)}"
    )
    lines = [headline, *_entries(removed)]
    if skipped:
        in_use = (
            f"Skipped {len(skipped)}, {_total_size(skipped)}; "
            f"in use by a running publish"
        )
        lines += [in_use, *_entries(skipped)]
    if failed:
        failure = (
            f"Could not remove {len(failed)}, {_total_size(failed)}; the log says why"
        )
        lines += [failure, *_entries(failed)]
    return "\n".join(lines)


def run(*, options: CleanOptions | None = None) -> str:
    """Report, and optionally remove, leftover staging directories.

    Parameters
    ----------
    options : CleanOptions | None
        Where to search and whether to remove. Defaults to reporting on the
        system temporary directory.

    A search directory that cannot be listed propagates
    :class:`CleanError` from :func:`find_leftovers` rather than reporting an
    empty sweep.

    Returns
    -------
    str
        The rendered summary.
    """
    active_options = options if options is not None else CleanOptions()
    location = _resolve_location(active_options.location)
    leftovers = find_leftovers(location)
    if not active_options.remove:
        return _summarize(leftovers, location)

    removed: list[LeftoverTree] = []
    skipped: list[LeftoverTree] = []
    for leftover in leftovers:
        # The claim is held across the removal rather than merely checked
        # before it. The answer is about another process, so asking and then
        # deleting leaves a window in which a publish can claim the tree and
        # have its workspace deleted out from under it.
        with staging_lock.hold_for_removal(leftover.path) as free:
            if not free:
                LOGGER.info("Skipping %s; a publish still holds it", leftover.path)
                skipped.append(leftover)
                continue
            LOGGER.info("Removing staging directory %s", leftover.path)
            try:
                shutil.rmtree(leftover.path)
            except OSError:
                LOGGER.exception("Could not remove %s; leaving it", leftover.path)
                continue
            removed.append(leftover)
    # All three sequences go in: the summary has to tell an empty search from
    # a search that found trees and removed none, and a tree left alone on
    # purpose from one whose removal failed.
    return _summarize(leftovers, location, removed=removed, skipped=skipped)


__all__: typ.Final = [
    "CleanError",
    "CleanOptions",
    "LeftoverTree",
    "find_leftovers",
    "run",
]
