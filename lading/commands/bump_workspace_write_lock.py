"""Serialize cooperating writes to one workspace across Lading processes."""

import collections.abc as cabc
import contextlib
import hashlib
import os
import stat
from pathlib import Path

from lading.utils import file_lock


@contextlib.contextmanager
def workspace_write_lock(workspace_root: Path) -> cabc.Generator[None]:
    """Hold the per-workspace advisory write lock until the block exits.

    All Lading writes bounded to the same resolved workspace serialize through
    this lock. Editors and tools that do not use it are outside that guarantee.
    Failure to create or acquire the lock is propagated immediately so a bump
    never proceeds with a write it cannot coordinate.

    Raises
    ------
    PermissionError
        If the lock directory is not private to its owner or the lock file
        cannot be opened or exclusively locked.
    """
    resolved_root = workspace_root.resolve()
    normalized_root = os.path.normcase(str(resolved_root))
    lock_key = hashlib.sha256(os.fsencode(normalized_root)).hexdigest()
    lock_directory = Path.home() / ".cache" / "lading" / "workspace-write-locks"
    lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        metadata = lock_directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            message = f"Workspace write lock directory is not private: {lock_directory}"
            raise PermissionError(message)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            message = f"Workspace write lock directory is not private: {lock_directory}"
            raise PermissionError(message)
    lock_path = lock_directory / f"{lock_key}.lock"
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        file_lock.set_exclusive_lock(handle, locked=True)
        try:
            yield
        finally:
            file_lock.set_exclusive_lock(handle, locked=False)
