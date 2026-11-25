"""Platform-specific primitives for non-blocking exclusive file locks."""

import sys
import typing as typ

if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def set_exclusive_lock(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Set or clear a non-blocking exclusive lock on ``handle``."""
        handle.seek(0)
        operation = msvcrt.LK_NBLCK if locked else msvcrt.LK_UNLCK
        msvcrt.locking(handle.fileno(), operation, 1)

else:
    import fcntl

    def set_exclusive_lock(handle: typ.IO[bytes], *, locked: bool) -> None:
        """Set or clear a non-blocking exclusive lock on ``handle``."""
        operation = fcntl.LOCK_EX | fcntl.LOCK_NB if locked else fcntl.LOCK_UN
        fcntl.flock(handle.fileno(), operation)
