"""Read manifests and atomically write TOML files."""

import os
import secrets
import stat
import tempfile
import typing as typ
from contextlib import suppress
from pathlib import Path

from tomlkit import parse as parse_toml

from lading.commands import manifest_paths
from lading.exceptions import LadingError

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


def parse_manifest(manifest_path: Path) -> TOMLDocument:
    """Load ``manifest_path`` into a :class:`tomlkit` document.

    Parameters
    ----------
    manifest_path : Path
        Filesystem path to the TOML manifest to read.

    Returns
    -------
    TOMLDocument
        The parsed manifest document.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     path = Path(directory) / "Cargo.toml"
    ...     _ = path.write_text('version = "0.1.0"', encoding="utf-8")
    ...     parse_manifest(path)["version"]
    '0.1.0'
    """
    return _read_and_parse_manifest(manifest_path)[1]


def _read_and_parse_manifest(manifest_path: Path) -> tuple[str, TOMLDocument]:
    """Return the exact text and TOML document produced by one file read."""
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    return content, parse_toml(content)


def write_atomic_text(
    file_path: Path,
    content: str,
    *,
    workspace_root: Path | None = None,
) -> None:
    """Atomically replace a text file using UTF-8 without newline translation.

    Without ``workspace_root``, symbolic-link paths are resolved before
    replacement, so the link remains in place and its target receives the new
    content. Existing permission bits are copied to the replacement. When a
    workspace root is supplied, the destination must still resolve to its
    planned path inside that workspace. Platforms with directory-relative
    operations replace relative to a pinned, no-follow directory descriptor.
    Other platforms revalidate the resolved destination immediately before the
    path-based atomic replacement.

    Parameters
    ----------
    file_path : Path
        Destination file to replace. Without ``workspace_root``, a symbolic
        link is followed and its target is replaced while the link is preserved.
    content : str
        Text to write to the destination.
    workspace_root : Path or None, optional
        Workspace boundary for callers that must prevent a changed symlink from
        redirecting the replacement. The destination must be a resolved path
        beneath this root. Platforms with directory-relative operations pin the
        parent while replacing; other platforms revalidate the path immediately
        before replacement. Defaults to ``None``.

    Raises
    ------
    OSError
        If resolving the destination, creating or writing the temporary file,
        preserving permissions, replacing the destination, or validating a
        bounded destination fails. Bounded writes use descriptor-relative
        replacement where the operating system supports it and path-based
        replacement with final containment validation otherwise.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     destination = Path(directory) / "Cargo.toml"
    ...     _ = destination.write_text("version = '0.1.0'", encoding="utf-8")
    ...     write_atomic_text(destination, "version = '1.0.0'")
    ...     destination.read_text(encoding="utf-8")
    "version = '1.0.0'"
    """  # ruff: ignore[docstring-extraneous-exception]  # filesystem calls propagate OSError
    if workspace_root is None:
        if file_path.is_symlink():
            file_path = file_path.resolve(strict=True)
        _write_path_based_atomic_text(file_path, content)
        return

    bounded_workspace_root = workspace_root.resolve()
    file_path = _resolve_bounded_write_target(file_path, bounded_workspace_root)
    if _supports_bounded_directory_operations():
        _write_bounded_atomic_text(file_path, content, bounded_workspace_root)
        return
    _write_path_based_atomic_text(file_path, content, bounded_workspace_root)


def _write_path_based_atomic_text(
    file_path: Path, content: str, workspace_root: Path | None = None
) -> None:
    """Replace by a temporary path and optionally revalidate containment."""
    dirpath = file_path.parent
    existing_mode: int | None = None
    with suppress(FileNotFoundError):
        existing_mode = stat.S_IMODE(file_path.stat().st_mode)

    temporary_directory, directory_fd = _open_temporary_parent_directory(dirpath)
    temporary_name: str | None = None
    temporary_path: Path | None = None
    primary_error: BaseException | None = None
    try:
        if directory_fd is None:
            descriptor, temporary_path_value = tempfile.mkstemp(
                dir=dirpath,
                prefix=".lading-",
                text=True,
            )
            replacement_path = Path(temporary_path_value)
            temporary_path = replacement_path
            temporary_name = temporary_path.name
        else:
            descriptor, temporary_name = _create_bounded_temporary_file(
                directory_fd, file_path.name
            )
            replacement_path = temporary_directory / temporary_name
            temporary_path = replacement_path

        handle = _open_temporary_text_file(descriptor)
        with handle:
            if existing_mode is not None:
                _set_file_mode(handle.fileno(), existing_mode)
            handle.write(content)
        if workspace_root is not None:
            _resolve_bounded_write_target(file_path, workspace_root)
        replacement_path.replace(file_path)
    except BaseException as error:
        primary_error = error
        if temporary_name is not None and temporary_path is not None:
            _cleanup_path_based_temporary_file(
                temporary_name, temporary_path, directory_fd, error
            )
        raise
    finally:
        if directory_fd is not None:
            _close_descriptor(directory_fd, primary_error)


def _open_temporary_parent_directory(directory: Path) -> tuple[Path, int | None]:
    """Pin a temporary file's parent when descriptor-relative cleanup exists."""
    resolved_directory = directory.resolve(strict=True)
    if not _supports_stable_temporary_cleanup():
        return resolved_directory, None
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        return resolved_directory, os.open(resolved_directory, flags)
    except OSError:
        return resolved_directory, None


def _supports_stable_temporary_cleanup() -> bool:
    """Check whether temporary files can be created and removed by directory fd."""
    return (
        hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
    )


def _open_temporary_text_file(descriptor: int) -> typ.TextIO:
    """Transfer a temporary descriptor to a text handle, closing on failure."""
    try:
        return os.fdopen(descriptor, "w", encoding="utf-8", newline="")
    except BaseException as error:
        _close_descriptor(descriptor, error)
        raise


def _cleanup_path_based_temporary_file(
    temporary_name: str,
    temporary_path: Path,
    directory_fd: int | None,
    primary_error: BaseException,
) -> None:
    """Remove a failed replacement's temporary file without masking its error."""
    try:
        if directory_fd is None:
            temporary_path.unlink()
        else:
            os.unlink(temporary_name, dir_fd=directory_fd)
    except OSError as cleanup_error:
        primary_error.add_note(
            f"Could not remove temporary file {temporary_name!r}: {cleanup_error}"
        )


def _close_descriptor(
    descriptor: int, primary_error: BaseException | None = None
) -> None:
    """Close an owned descriptor, attaching close failures to an active error."""
    try:
        os.close(descriptor)
    except OSError as close_error:
        if primary_error is None:
            raise
        primary_error.add_note(
            f"Could not close descriptor {descriptor}: {close_error}"
        )


def _resolve_bounded_write_target(file_path: Path, workspace_root: Path) -> Path:
    """Resolve an in-workspace destination before creating its replacement."""
    try:
        resolved_path = manifest_paths.resolve_workspace_path(
            workspace_root,
            file_path,
            error_type=LadingError,
            label="Atomic write",
        )
    except LadingError as exc:
        message = (
            f"Atomic write destination must stay within the workspace: {file_path}"
        )
        raise PermissionError(message) from exc
    if resolved_path != file_path:
        message = (
            "Atomic write destination no longer resolves to its planned path: "
            f"{file_path}"
        )
        raise PermissionError(message)
    return resolved_path


def _write_bounded_atomic_text(
    file_path: Path, content: str, workspace_root: Path
) -> None:
    """Replace an in-workspace file relative to its opened parent directory."""
    relative_path = file_path.relative_to(workspace_root)
    directory_fd = _open_workspace_parent_directory(
        workspace_root, relative_path.parent
    )
    temporary_name: str | None = None
    try:
        existing_mode = _existing_file_mode(directory_fd, file_path.name)
        temporary_fd, temporary_name = _create_bounded_temporary_file(
            directory_fd, file_path.name
        )
        try:
            handle = os.fdopen(temporary_fd, "w", encoding="utf-8", newline="")
        except BaseException:
            os.close(temporary_fd)
            raise
        with handle:
            if existing_mode is not None:
                _set_file_mode(handle.fileno(), existing_mode)
            handle.write(content)
        os.rename(
            temporary_name,
            relative_path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
    finally:
        try:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)


def _supports_bounded_directory_operations() -> bool:
    """Check whether this platform supports the required descriptor APIs."""
    required = (os.open, os.stat, os.unlink, os.rename)
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and all(operation in os.supports_dir_fd for operation in required)
        and os.stat in os.supports_follow_symlinks
    )


def _open_directory_chain(directory: Path) -> int:
    """Open a directory path one no-follow component at a time."""
    flags = _no_follow_directory_flags()
    current_fd = os.open(directory.anchor, flags)
    return _open_directory_components(current_fd, directory.parts[1:])


def _open_directory_components(current_fd: int, components: tuple[str, ...]) -> int:
    """Descend from an open directory while closing each prior descriptor."""
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
    workspace_root: Path, relative_parent: Path
) -> int:
    """Open a manifest parent beneath a pinned workspace directory handle."""
    current_fd = _open_directory_chain(workspace_root)
    return _open_directory_components(current_fd, relative_parent.parts)


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


def _create_bounded_temporary_file(
    directory_fd: int, destination_name: str
) -> tuple[int, str]:
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
