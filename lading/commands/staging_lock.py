"""A cross-process claim on a staging tree.

`lading clean --remove` deletes directories it did not create, and a staged
tree belonging to a running publish looks exactly like one abandoned by a
release that never cleaned up. The publish is in another process, so the
in-memory ``_ACTIVE_STAGING_ROOTS`` set cannot answer for it.

Every automatically created staging tree therefore carries a lock file, which
its publisher holds open and locked for the tree's life. `clean` tries the
same lock immediately before removing a tree and leaves the tree alone if it
cannot take it.

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

import logging
import sys
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
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


def claim(root: Path) -> None:
    """Claim ``root`` for this process until :func:`release`, or until exit.

    A claim that cannot be made is logged and otherwise ignored. The lock is a
    courtesy to a concurrent `clean`, and a filesystem that will not lock must
    not stop a publish.
    """
    try:
        handle = _open(root)
    except OSError:
        LOGGER.warning("Could not create a staging lock in %s", root, exc_info=True)
        return
    try:
        _take(handle)
    except OSError:
        LOGGER.warning("Could not claim the staging lock in %s", root, exc_info=True)
        handle.close()
        return
    _HELD[root] = handle


def release(root: Path) -> None:
    """Drop this process's claim on ``root``, if it holds one.

    Called before the tree is removed. On Windows an open handle inside a
    directory stops that directory being deleted.
    """
    handle = _HELD.pop(root, None)
    if handle is None:
        return
    try:
        _drop(handle)
    finally:
        handle.close()


def is_in_use(root: Path) -> bool:
    """Report whether a live publish still holds ``root``.

    Returns
    -------
    bool
        Whether some process holds the tree's lock. A tree carrying no lock
        file predates the lock and is reported as free.
    """
    if root in _HELD:
        return True
    lock = root / LOCK_NAME
    if not lock.is_file():
        return False
    try:
        handle = _open(root)
    except OSError:
        # The lock file cannot even be opened, so its tree is not ours to
        # judge. Reporting it in use is the answer that does not delete.
        LOGGER.warning("Could not read the staging lock in %s", root, exc_info=True)
        return True
    with handle:
        try:
            _take(handle)
        except OSError:
            return True
        _drop(handle)
    return False
