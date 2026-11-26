"""Directory-relative primitives for bounded atomic text replacement."""

import collections.abc as cabc
import os
import secrets
import stat
import typing as typ
from contextlib import contextmanager, suppress
from pathlib import Path

from lading.commands.bump_toml_write_options import AtomicWriteIdentity


def _open_directory_chain(directory: Path) -> int:
    """Open a directory path one no-follow component at a time."""
    flags = _no_follow_directory_flags()
    current_fd = os.open(directory.anchor, flags)
    return _open_directory_components(current_fd, directory.parts[1:])


def _open_directory_components(current_fd: int, components: tuple[str, ...]) -> int:
    """Descend and consume ``current_fd`` on both success and failure.

    Callers that require confinement must supply only relative path components
    without ``..``; absolute components also bypass the directory descriptor.

    Parameters
    ----------
    current_fd : int
        Owned directory descriptor, consumed on every return path.
    components : tuple[str, ...]
        Directory names to open beneath ``current_fd``.

    Returns
    -------
    int
        Owned descriptor for the final directory, transferred to the caller.
    """
    flags = _no_follow_directory_flags()
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


def _open_workspace_parent_directory(
    workspace_root: Path,
    relative_parent: Path,
    expected_root_identity: AtomicWriteIdentity | None = None,
) -> int:
    """Open a parent beneath the root identity captured by its write lock."""
    current_fd = _open_directory_chain(workspace_root)
    if expected_root_identity is not None:
        try:
            metadata = os.fstat(current_fd)
        except BaseException:
            with suppress(OSError):
                os.close(current_fd)
            raise
        opened_identity = AtomicWriteIdentity(metadata.st_dev, metadata.st_ino)
        if opened_identity != expected_root_identity:
            with suppress(OSError):
                os.close(current_fd)
            message = (
                "Workspace root changed after acquiring the write lock: "
                f"{workspace_root}"
            )
            raise PermissionError(message)
    return _open_directory_components(current_fd, relative_parent.parts)


def _normalize_bounded_write_path(file_path: Path, workspace_root: Path) -> Path:
    """Normalize safe ``..`` segments without allowing symlinks to hide."""
    candidate = file_path if file_path.is_absolute() else workspace_root / file_path
    parts = _relative_bounded_write_parts(candidate, file_path, workspace_root)

    normalized_parts: list[str] = []
    for index, part in enumerate(parts):
        if part in {"", "."}:
            continue
        if part == "..":
            _pop_bounded_write_parent(file_path, normalized_parts)
            continue
        _append_bounded_write_component(
            workspace_root,
            normalized_parts,
            part,
            has_remaining_components=index < len(parts) - 1,
        )
    return workspace_root.joinpath(*normalized_parts)


def _relative_bounded_write_parts(
    candidate: Path,
    file_path: Path,
    workspace_root: Path,
) -> tuple[str, ...]:
    """Return lexical path parts after enforcing the root boundary."""
    try:
        return candidate.relative_to(workspace_root).parts
    except ValueError as exc:
        message = (
            f"Atomic write destination must stay within the workspace: {file_path}"
        )
        raise PermissionError(message) from exc


def _pop_bounded_write_parent(file_path: Path, normalized_parts: list[str]) -> None:
    """Remove one parent component without crossing the workspace root."""
    if not normalized_parts:
        message = (
            f"Atomic write destination must stay within the workspace: {file_path}"
        )
        raise PermissionError(message)
    normalized_parts.pop()


def _append_bounded_write_component(
    workspace_root: Path,
    normalized_parts: list[str],
    part: str,
    *,
    has_remaining_components: bool,
) -> None:
    """Validate one component before adding it to the normalized path."""
    component = workspace_root.joinpath(*normalized_parts, part)
    try:
        metadata = component.lstat()
    except FileNotFoundError:
        if has_remaining_components:
            raise
    else:
        if stat.S_ISLNK(metadata.st_mode):
            message = (
                "Atomic write destination must stay within the workspace and "
                f"must not traverse symbolic links: {component}"
            )
            raise PermissionError(message)
        if has_remaining_components and not stat.S_ISDIR(metadata.st_mode):
            message = (
                f"Atomic write destination has a non-directory parent: {component}"
            )
            raise PermissionError(message)
    normalized_parts.append(part)


def _no_follow_directory_flags() -> int:
    """Return flags that require one real directory component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _existing_file_mode(directory_fd: int, filename: str) -> int | None:
    """Read the destination mode without following a symlink."""
    try:
        metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        message = "Atomic write destination changed to a symbolic link"
        raise PermissionError(message)
    return stat.S_IMODE(metadata.st_mode)


def _read_bounded_regular_text(
    file_path: Path,
    workspace_root: Path,
) -> tuple[str, AtomicWriteIdentity]:
    """Read UTF-8 text and identity through a no-follow workspace handle."""
    if not _supports_bounded_directory_operations():
        message = "Workspace-bounded manifest reads are unavailable on this platform"
        raise PermissionError(message)
    root = workspace_root.resolve()
    candidate = file_path if file_path.is_absolute() else root / file_path
    absolute_path = candidate.absolute()
    try:
        relative_path = absolute_path.relative_to(root)
    except ValueError as exc:
        message = f"Manifest must stay within the workspace: {file_path}"
        raise PermissionError(message) from exc
    if ".." in relative_path.parts or not relative_path.parts:
        message = f"Manifest must stay within the workspace: {file_path}"
        raise PermissionError(message)

    directory_fd = _open_workspace_parent_directory(root, relative_path.parent)
    try:
        with _open_regular_text_file_at(
            directory_fd,
            relative_path.name,
            f"Manifest is not a regular file: {file_path}",
        ) as (handle, identity):
            return handle.read(), identity
    finally:
        os.close(directory_fd)


def _validate_expected_bounded_text(
    directory_fd: int,
    filename: str,
    expected_text: str | None,
    expected_identity: AtomicWriteIdentity | None,
) -> None:
    """Compare conditional text and identity through a pinned directory handle."""
    with _open_regular_text_file_at(
        directory_fd,
        filename,
        f"Atomic write destination is not a regular file: {filename}",
    ) as (handle, current_identity):
        if expected_identity is not None and current_identity != expected_identity:
            message = f"Atomic write destination identity changed: {filename}"
            raise PermissionError(message)
        if expected_text is not None and handle.read() != expected_text:
            message = f"Atomic write destination changed before replacement: {filename}"
            raise PermissionError(message)


@contextmanager
def _open_regular_text_file_at(
    directory_fd: int,
    filename: str,
    not_regular_message: str,
) -> cabc.Generator[tuple[typ.TextIO, AtomicWriteIdentity]]:
    """Open one non-symlink regular UTF-8 text file relative to a directory."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(filename, flags, dir_fd=directory_fd)
    try:
        handle = os.fdopen(descriptor, "r", encoding="utf-8", newline="")
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise
    with handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionError(not_regular_message)
        identity = AtomicWriteIdentity(metadata.st_dev, metadata.st_ino)
        yield handle, identity


def _create_temporary_file(directory_fd: int, destination_name: str) -> tuple[int, str]:
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


def _set_file_mode(descriptor: int, mode: int) -> None:
    """Apply existing mode bits to the temporary file descriptor."""
    os.fchmod(descriptor, mode)


def _supports_bounded_directory_operations() -> bool:
    """Check whether the platform exposes the required descriptor APIs."""
    required = (os.open, os.stat, os.unlink, os.rename)
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and all(operation in os.supports_dir_fd for operation in required)
        and os.stat in os.supports_follow_symlinks
    )
