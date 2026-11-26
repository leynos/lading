"""Serialize cooperating writes to one workspace across Lading processes."""

import collections.abc as cabc
import contextlib
import hashlib
import os
import stat
import threading
import typing as typ
from pathlib import Path

if os.name != "nt":
    import pwd

from lading.commands.bump_toml_write_options import AtomicWriteIdentity
from lading.utils import file_lock

_HELD_WORKSPACE_LOCKS = threading.local()


@contextlib.contextmanager
def workspace_write_lock(
    resolved_workspace_root: Path,
) -> cabc.Generator[AtomicWriteIdentity]:
    """Hold the per-workspace advisory write lock until the block exits.

    Lading processes running as the same operating-system user serialize
    bounded writes to the same resolved workspace through this lock. Editors,
    tools that do not use it, and processes running as another user are outside
    that guarantee.
    Failure to create or acquire the lock is propagated immediately so a bump
    never proceeds with a write it cannot coordinate. Nested writes on the
    same thread reuse the lock so validation and replacements can be one
    coordinated operation.

    Parameters
    ----------
    resolved_workspace_root
        Absolute workspace path. It is resolved again here so an unresolved
        alias of a held lock still reuses that lock instead of acquiring a
        second handle on the same lock file and deadlocking.

    Yields
    ------
    AtomicWriteIdentity
        Device and inode captured when the advisory lock key was selected.
        Bounded writers verify that the directory they open still has this
        identity before replacing a file.

    """
    workspace_root = resolved_workspace_root.resolve()
    root_key = str(workspace_root)
    held_locks = _held_workspace_locks()
    held_lock = held_locks.get(root_key)
    if held_lock is not None:
        with _reuse_workspace_write_lock(root_key, held_lock, held_locks) as identity:
            yield identity
        return
    with _acquire_workspace_write_lock(
        workspace_root, root_key, held_locks
    ) as identity:
        yield identity


@contextlib.contextmanager
def _reuse_workspace_write_lock(
    root_key: str,
    held_lock: tuple[AtomicWriteIdentity, int],
    held_locks: dict[str, tuple[AtomicWriteIdentity, int]],
) -> cabc.Generator[AtomicWriteIdentity]:
    """Borrow a lock already held by this thread for the same resolved path."""
    identity, depth = held_lock
    held_locks[root_key] = (identity, depth + 1)
    try:
        yield identity
    finally:
        held_locks[root_key] = (identity, depth)


@contextlib.contextmanager
def _acquire_workspace_write_lock(
    workspace_root: Path,
    root_key: str,
    held_locks: dict[str, tuple[AtomicWriteIdentity, int]],
) -> cabc.Generator[AtomicWriteIdentity]:
    """Acquire the OS lock and register its identity for nested writers."""
    identity = _workspace_identity(workspace_root)
    with _workspace_lock_handle(identity) as handle:
        file_lock.set_exclusive_lock(handle, locked=True)
        held_locks[root_key] = (identity, 1)
        primary_error: BaseException | None = None
        try:
            yield identity
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


@contextlib.contextmanager
def _workspace_lock_handle(
    identity: AtomicWriteIdentity,
) -> cabc.Generator[typ.BinaryIO]:
    """Open the private per-user lock file selected by a filesystem identity."""
    lock_directory = _workspace_lock_directory()
    lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    _validate_lock_directory(lock_directory)
    lock_path = lock_directory / f"{_lock_key_for_identity(identity)}.lock"
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        yield handle


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


def _workspace_identity(resolved_workspace_root: Path) -> AtomicWriteIdentity:
    """Capture the filesystem identity that the lock and writer must share."""
    metadata = resolved_workspace_root.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        message = (
            f"Workspace write lock root is not a directory: {resolved_workspace_root}"
        )
        raise PermissionError(message)
    return AtomicWriteIdentity(metadata.st_dev, metadata.st_ino)


def _lock_key_for_identity(identity: AtomicWriteIdentity) -> str:
    """Hash one captured filesystem identity into its advisory lock name."""
    encoded_identity = f"{identity.device}:{identity.inode}".encode("ascii")
    return hashlib.sha256(encoded_identity).hexdigest()


def _held_workspace_locks() -> dict[str, tuple[AtomicWriteIdentity, int]]:
    """Return the current thread's re-entrant workspace lock registry."""
    held_locks = getattr(_HELD_WORKSPACE_LOCKS, "by_root", None)
    if held_locks is None:
        held_locks = {}
        _HELD_WORKSPACE_LOCKS.by_root = held_locks
    return held_locks


def _workspace_lock_directory() -> Path:
    """Return a per-user lock directory independent of process cache settings."""
    if os.name == "nt":
        try:
            home_directory = Path.home()
        except RuntimeError as exc:
            message = "Cannot locate a stable per-user workspace write lock directory"
            raise PermissionError(message) from exc
    else:
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
    if os.name == "nt":
        return home_directory / "AppData" / "Local" / "lading" / "workspace-write-locks"
    return home_directory / ".local" / "state" / "lading" / "workspace-write-locks"
