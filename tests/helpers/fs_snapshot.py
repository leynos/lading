"""Non-following filesystem snapshots for preflight regression tests.

A snapshot records every entry below a root with ``lstat``. Directories are
recursed, symlinks record their target text without being followed, and only
regular files are opened for their bytes. A FIFO, socket, or device is recorded
by kind and never opened, so taking a snapshot cannot block on a special file.
"""

import collections.abc as cabc
import dataclasses as dc
import os
import stat
from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class SnapshotEntry:
    """One filesystem entry observed without following links.

    Attributes
    ----------
    relative : str
        POSIX path relative to the snapshotted root; ``"."`` is the root itself.
    kind : str
        ``"file"``, ``"directory"``, ``"symlink"``, or ``"special"``.
    mode : int
        The ``lstat`` mode bits.
    content : bytes | None
        Regular-file bytes, or ``None`` for every other kind.
    link_target : str | None
        The raw symlink target, or ``None`` for every other kind.
    """

    relative: str
    kind: str
    mode: int
    content: bytes | None = None
    link_target: str | None = None


def snapshot_tree(root: Path) -> tuple[SnapshotEntry, ...]:
    """Return a stable, non-following snapshot of ``root``.

    Parameters
    ----------
    root : Path
        Directory to snapshot. A missing root yields an empty snapshot.

    Returns
    -------
    tuple[SnapshotEntry, ...]
        Entries sorted by relative POSIX path.
    """
    try:
        root_stat = os.lstat(root)
    except FileNotFoundError:
        return ()
    entries = [SnapshotEntry(relative=".", kind="directory", mode=root_stat.st_mode)]
    if stat.S_ISDIR(root_stat.st_mode):
        _snapshot_directory(root, (), entries)
    return tuple(sorted(entries, key=lambda entry: entry.relative))


def _snapshot_directory(
    root: Path,
    prefix: tuple[str, ...],
    entries: list[SnapshotEntry],
) -> None:
    """Append every entry below ``root/prefix`` to ``entries``.

    A directory that cannot be listed keeps only its own entry. Snapshotting is
    an observation of the operation under test, so it must not raise for a
    permission failure the test itself injected.
    """
    try:
        scanned = list(os.scandir(root.joinpath(*prefix)))
    except OSError:
        return
    for entry in scanned:
        parts = (*prefix, entry.name)
        _snapshot_entry(root, parts, entry, entries)


def _snapshot_entry(
    root: Path,
    parts: tuple[str, ...],
    entry: os.DirEntry[str],
    entries: list[SnapshotEntry],
) -> None:
    """Append one directory entry, recursing into real directories."""
    result = entry.stat(follow_symlinks=False)
    relative = "/".join(parts)
    if stat.S_ISLNK(result.st_mode):
        entries.append(
            SnapshotEntry(
                relative=relative,
                kind="symlink",
                mode=result.st_mode,
                link_target=Path(entry.path).readlink().as_posix(),
            )
        )
        return
    if stat.S_ISDIR(result.st_mode):
        entries.append(
            SnapshotEntry(relative=relative, kind="directory", mode=result.st_mode)
        )
        _snapshot_directory(root, parts, entries)
        return
    if stat.S_ISREG(result.st_mode):
        entries.append(
            SnapshotEntry(
                relative=relative,
                kind="file",
                mode=result.st_mode,
                content=Path(entry.path).read_bytes(),
            )
        )
        return
    entries.append(
        SnapshotEntry(relative=relative, kind="special", mode=result.st_mode)
    )


def changed_paths(
    before: cabc.Sequence[SnapshotEntry],
    after: cabc.Sequence[SnapshotEntry],
) -> tuple[str, ...]:
    """Return the relative paths whose entry differs between two snapshots.

    Parameters
    ----------
    before : cabc.Sequence[SnapshotEntry]
        Snapshot taken before the operation under test.
    after : cabc.Sequence[SnapshotEntry]
        Snapshot taken after the operation under test.

    Returns
    -------
    tuple[str, ...]
        Sorted relative paths present in one snapshot and changed or missing in
        the other.
    """
    before_by_path = {entry.relative: entry for entry in before}
    after_by_path = {entry.relative: entry for entry in after}
    changed = {
        relative
        for relative in before_by_path.keys() | after_by_path.keys()
        if before_by_path.get(relative) != after_by_path.get(relative)
    }
    return tuple(sorted(changed))
