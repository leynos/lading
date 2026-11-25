"""Directory-relative primitives for bounded atomic text replacement."""

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path


def open_directory_chain(directory: Path) -> int:
    """Open a directory path one no-follow component at a time."""
    flags = no_follow_directory_flags()
    current_fd = os.open(directory.anchor, flags)
    return open_directory_components(current_fd, directory.parts[1:])


def open_directory_components(current_fd: int, components: tuple[str, ...]) -> int:
    """Descend from an open directory while closing each prior descriptor."""
    flags = no_follow_directory_flags()
    transferred = False
    try:
        for component in components:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            previous_fd = current_fd
            current_fd = next_fd
            os.close(previous_fd)
        transferred = True
        return current_fd
    finally:
        if not transferred:
            with suppress(OSError):
                os.close(current_fd)


def open_workspace_parent_directory(workspace_root: Path, relative_parent: Path) -> int:
    """Open a manifest parent beneath a pinned workspace directory handle."""
    current_fd = open_directory_chain(workspace_root)
    return open_directory_components(current_fd, relative_parent.parts)


def no_follow_directory_flags() -> int:
    """Return flags that require one real directory component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def existing_file_mode(directory_fd: int, filename: str) -> int | None:
    """Read the destination mode without following a symlink."""
    try:
        metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        message = "Atomic write destination changed to a symbolic link"
        raise PermissionError(message)
    return stat.S_IMODE(metadata.st_mode)


def create_temporary_file(directory_fd: int, destination_name: str) -> tuple[int, str]:
    """Create a unique temporary file relative to an already-open directory."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    for _attempt in range(100):
        temporary_name = f".lading-{secrets.token_hex(8)}"
        try:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
        return descriptor, temporary_name
    message = f"Could not allocate a temporary file for {destination_name}"
    raise FileExistsError(message)


def set_file_mode(descriptor: int, mode: int) -> None:
    """Apply existing mode bits to the temporary file descriptor."""
    os.fchmod(descriptor, mode)
