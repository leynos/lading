"""Serialize cooperating bump runs for one workspace across Lading processes."""

import collections.abc as cabc
import contextlib
import hashlib
import os
import stat
import threading
from pathlib import Path

if os.name != "nt":
    import pwd

from lading.utils import file_lock

_HELD_WORKSPACE_LOCKS = threading.local()


@contextlib.contextmanager
def workspace_write_lock(
    resolved_workspace_root: Path,
) -> cabc.Generator[None]:
    """Hold the per-workspace advisory bump lock until the block exits.

    Lading processes running as the same operating-system user serialize whole
    live bump runs through this lock, so a concurrent run cannot plan from, or
    commit over, another run's accepted versions. Editors, tools that do not
    use the lock, and processes running as another user are outside that
    guarantee. Failure to create or acquire the lock is propagated immediately
    so a bump never proceeds with writes it cannot coordinate. Nested use on
    the same thread reuses the held lock.

    Parameters
    ----------
    resolved_workspace_root
        Workspace path. It is resolved here so an unresolved alias of a held
        lock still reuses that lock rather than opening a second handle on the
        same lock file.
    """
    workspace_root = resolved_workspace_root.resolve()
    root_key = str(workspace_root)
    held_locks = _held_workspace_locks()
    depth = held_locks.get(root_key)
    if depth is not None:
        held_locks[root_key] = depth + 1
        try:
            yield
        finally:
            held_locks[root_key] = depth
        return
    with _acquire_workspace_write_lock(root_key, held_locks):
        yield


@contextlib.contextmanager
def _acquire_workspace_write_lock(
    root_key: str,
    held_locks: dict[str, int],
) -> cabc.Generator[None]:
    """Acquire the OS lock and register it for nested writers."""
    lock_directory = _workspace_lock_directory()
    _prepare_lock_directory(lock_directory)
    _validate_lock_directory(lock_directory)
    lock_path = lock_directory / f"{_lock_key_for_path(root_key)}.lock"
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        file_lock.set_exclusive_lock(handle, locked=True)
        held_locks[root_key] = 1
        primary_error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                file_lock.set_exclusive_lock(handle, locked=False)
            except BaseException as unlock_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"Could not release workspace write lock: {unlock_error}"
                )
            finally:
                held_locks.pop(root_key, None)


def _prepare_lock_directory(lock_directory: Path) -> None:
    """Create the private per-user lock directory or explain the prerequisite."""
    try:
        lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        message = (
            f"Cannot create the per-user workspace write lock directory "
            f"{lock_directory}: {exc}. Ensure the account's passwd home is "
            "writable, or provision that private account-owned directory."
        )
        raise PermissionError(message) from exc


def _validate_lock_directory(lock_directory: Path) -> None:
    """Require the lock directory to be private to the current account."""
    if os.name == "nt":
        return
    metadata = lock_directory.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        message = f"Workspace write lock directory is not private: {lock_directory}"
        raise PermissionError(message)
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        message = f"Workspace write lock directory is not private: {lock_directory}"
        raise PermissionError(message)


def _lock_key_for_path(root_key: str) -> str:
    """Hash one resolved workspace path into its advisory lock name."""
    return hashlib.sha256(os.fsencode(root_key)).hexdigest()


def _held_workspace_locks() -> dict[str, int]:
    """Return the current thread's re-entrant workspace lock registry."""
    held_locks = getattr(_HELD_WORKSPACE_LOCKS, "by_root", None)
    if held_locks is None:
        held_locks = {}
        _HELD_WORKSPACE_LOCKS.by_root = held_locks
    return held_locks


def _workspace_lock_directory() -> Path:
    """Return a per-user lock directory independent of process cache settings."""
    if os.name == "nt":
        return _windows_lock_directory()
    return _posix_lock_directory()


def _windows_lock_directory() -> Path:
    """Return the per-user lock directory used on Windows."""
    try:
        home_directory = Path.home()
    except RuntimeError as exc:
        message = "Cannot locate a stable per-user workspace write lock directory"
        raise PermissionError(message) from exc
    return home_directory / "AppData" / "Local" / "lading" / "workspace-write-locks"


def _posix_lock_directory() -> Path:
    """Return the per-user lock directory derived from the passwd entry."""
    home_directory = _posix_home_directory()
    return home_directory / ".local" / "state" / "lading" / "workspace-write-locks"


def _posix_home_directory() -> Path:
    """Return an absolute passwd home directory for the current account."""
    try:
        home_directory = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError as exc:
        message = (
            "Cannot locate a stable per-user workspace write lock directory: "
            "the current user has no passwd entry"
        )
        raise PermissionError(message) from exc
    except OSError as exc:
        message = "Cannot locate a stable per-user workspace write lock directory"
        raise PermissionError(message) from exc
    if not home_directory.is_absolute():
        message = "Cannot locate a stable per-user workspace write lock directory"
        raise PermissionError(message)
    return home_directory
