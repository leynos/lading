"""Platform-specific primitives for non-blocking exclusive file locks."""

import sys
import typing as typ

if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def set_exclusive_lock(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Set or clear a non-blocking exclusive lock on a binary handle.

        Parameters
        ----------
        handle : IO[bytes]
            Open binary file handle whose descriptor is used for locking. The
            caller retains ownership and must keep it open while locked.
        locked : bool
            ``True`` acquires the lock; ``False`` releases it.

        Notes
        -----
        Failure propagation: exceptions raised by seeking or the platform
        locking call propagate unchanged, including failures to complete a
        non-blocking acquisition.

        Handle ownership: the caller owns and closes the handle after releasing
        any acquired lock.

        On Windows, the lock is applied to the first byte after seeking to the
        start of the file.
        """
        handle.seek(0)
        operation = msvcrt.LK_NBLCK if locked else msvcrt.LK_UNLCK
        msvcrt.locking(handle.fileno(), operation, 1)

else:
    import fcntl

    def set_exclusive_lock(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Set or clear a non-blocking exclusive lock on a binary handle.

        Parameters
        ----------
        handle : IO[bytes]
            Open binary file handle whose descriptor is used for locking. The
            caller retains ownership and must keep it open while locked.
        locked : bool
            ``True`` acquires the lock; ``False`` releases it.

        Notes
        -----
        Failure propagation: exceptions raised by the platform locking call
        propagate unchanged, including failures to complete a non-blocking
        acquisition.

        Handle ownership: the caller owns and closes the handle after releasing
        any acquired lock.

        The lock is associated with the open file description, so the caller
        must retain the handle while the lock is held.
        """
        operation = fcntl.LOCK_EX | fcntl.LOCK_NB if locked else fcntl.LOCK_UN
        fcntl.flock(handle.fileno(), operation)
