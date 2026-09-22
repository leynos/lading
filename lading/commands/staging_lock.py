"""A cross-process claim on a staging tree.

`lading clean --remove` deletes directories it did not create, and a staged
tree belonging to a running publish looks exactly like one abandoned by a
release that never cleaned up. The publish is in another process, so the
in-memory ``_ACTIVE_STAGING_ROOTS`` set cannot answer for it.

Every automatically created staging tree therefore carries a lock file, which
its publisher holds open and locked for the tree's life. `clean` takes the
same lock and *holds* it across the removal, through
:func:`hold_for_removal`, rather than asking and then deleting: between those
two moments a publish could claim the tree, and the deletion would take a
workspace still being read.

Windows is the exception, and it is safe for a different reason. An open
handle inside a directory stops that directory being deleted there, so the
claim cannot be held across the removal; but the same rule means a live
publisher's own handle makes the removal fail rather than succeed, so an
in-use tree is still never deleted.

A lock rather than a recorded process identifier, because the two failure
modes of a marker file point in opposite directions: a reused identifier makes
a dead owner look alive, and a publish killed outright leaves a marker that
would make its tree permanently unremovable, which is the exact leftover this
command exists to sweep. The kernel drops a lock when its holder dies,
``SIGKILL`` included, so there is no stale state to clear and nothing to time
out.

A tree with no lock file was left by a release predating this and stays
removable. The lock is advisory: deleting the file by hand defeats it.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import collections.abc as cabc
    import io
    from pathlib import Path

LOGGER = logging.getLogger(__name__)

#: Name of the lock file inside an automatically created staging tree.
LOCK_NAME: typ.Final = ".lading-staging-lock"

if sys.platform == "win32":  # pragma: no cover - runs on the Windows lanes
    import msvcrt

    def _take(handle: io.BufferedRandom) -> None:
        """Take the lock, raising :class:`OSError` if another holder has it."""
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _drop(handle: io.BufferedRandom) -> None:
        """Release a lock taken through ``handle``."""
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _take(handle: io.BufferedRandom) -> None:
        """Take the lock, raising :class:`OSError` if another holder has it."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _drop(handle: io.BufferedRandom) -> None:
        """Release a lock taken through ``handle``."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


#: Claims this process holds, keyed by the tree each one covers. The handle
#: stays open deliberately: closing it would release the lock.
_HELD: dict[Path, io.BufferedRandom] = {}


def _open(root: Path) -> io.BufferedRandom:
    """Return an open handle on ``root``'s lock file, creating it if absent.

    The position is set to the start because Windows locks a byte range from
    wherever the handle happens to be, and both sides must name the same byte.

    Returns
    -------
    io.BufferedRandom
        The open handle.
    """
    handle = (root / LOCK_NAME).open("a+b")
    handle.seek(0)
    return handle


def claim(root: Path) -> bool:
    """Claim ``root`` for this process until :func:`release`, or until exit.

    A claim that cannot be made is reported rather than raised. The lock is a
    courtesy to a concurrent `clean`, and a filesystem that will not lock must
    not stop a publish; the caller is told so it can say what it is doing
    rather than assume the tree is protected.

    Parameters
    ----------
    root : Path
        The staging tree to claim. Its lock file is created if absent, so the
        directory must already exist.

    Returns
    -------
    bool
        Whether the claim was made.
    """
    try:
        handle = _open(root)
    except OSError:
        LOGGER.warning("Could not create a staging lock in %s", root, exc_info=True)
        return False
    try:
        _take(handle)
    except OSError:
        LOGGER.warning("Could not claim the staging lock in %s", root, exc_info=True)
        handle.close()
        return False
    _HELD[root] = handle
    return True


def release(root: Path) -> None:
    """Drop this process's claim on ``root``, if it holds one.

    Called before the tree is removed. On Windows an open handle inside a
    directory stops that directory being deleted.

    Parameters
    ----------
    root : Path
        The staging tree to release. A tree this process does not hold is
        ignored, so the call is safe to make unconditionally.
    """
    handle = _HELD.pop(root, None)
    if handle is None:
        return
    try:
        _drop(handle)
    finally:
        handle.close()


def _acquire_for_removal(root: Path) -> tuple[bool, io.BufferedRandom | None]:
    """Report whether ``root`` may be removed, with the handle holding it so.

    Returns
    -------
    tuple[bool, io.BufferedRandom | None]
        Whether the tree is free to remove, and the open handle whose lock
        keeps it that way. The handle is :data:`None` when there is nothing
        to hold: either the tree is not free, or it carries no lock file.
    """
    if root in _HELD:
        # This process is publishing into the tree. Asking the kernel would
        # only take the claim we already hold, which answers nothing.
        return (False, None)
    if not (root / LOCK_NAME).is_file():
        # A tree from a release predating the claim. Nothing holds it, and
        # nothing here can, which is the documented bargain.
        return (True, None)
    try:
        handle = _open(root)
    except OSError:
        # The lock file cannot even be opened, so its tree is not ours to
        # judge. Reporting it in use is the answer that does not delete.
        LOGGER.warning("Could not read the staging lock in %s", root, exc_info=True)
        return (False, None)
    try:
        _take(handle)
    except OSError:
        handle.close()
        return (False, None)
    if sys.platform == "win32":  # pragma: no cover - runs on the Windows lanes
        # The claim cannot be held across the removal here, because the open
        # handle would itself stop the directory being deleted. The same rule
        # is what keeps the tree safe: a live publisher's handle makes the
        # removal fail rather than succeed.
        _drop(handle)
        handle.close()
        return (True, None)
    return (True, handle)


@contextlib.contextmanager
def hold_for_removal(root: Path) -> cabc.Iterator[bool]:
    """Claim ``root`` for deletion, holding the claim for the block's life.

    Asking whether a tree is in use and then deleting it are two moments, and
    a publish can claim the tree in between. Callers that delete must do so
    inside this block, so the claim they were given is still theirs when the
    directory goes.

    Parameters
    ----------
    root : Path
        The staging tree to claim for deletion. A tree carrying no lock file
        is reported free without anything being held.

    Yields
    ------
    bool
        Whether the tree is free to remove. :data:`False` means a live
        publish holds it, or that its claim could not be read.
    """
    free, handle = _acquire_for_removal(root)
    try:
        yield free
    finally:
        if handle is not None:
            try:
                _drop(handle)
            finally:
                handle.close()


def is_in_use(root: Path) -> bool:
    """Report whether a live publish still holds ``root``.

    The answer is true only for the instant it is given, so anything that
    acts on it must use :func:`hold_for_removal` instead. This remains for
    callers that only report.

    Parameters
    ----------
    root : Path
        The staging tree to ask about.

    Returns
    -------
    bool
        Whether some process holds the tree's lock. A tree carrying no lock
        file predates the lock and is reported as free.
    """
    with hold_for_removal(root) as free:
        return not free
