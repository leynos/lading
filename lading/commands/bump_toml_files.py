"""Read manifests and atomically write TOML files."""

import os
import stat
import tempfile
import typing as typ
from contextlib import suppress
from pathlib import Path

from tomlkit import parse as parse_toml

from lading.commands import (
    bump_toml_temporary_cleanup,
    bump_workspace_write_lock,
    manifest_paths,
)
from lading.commands.bump_toml_bounded_filesystem import (
    create_temporary_file as _create_bounded_temporary_file,
)
from lading.commands.bump_toml_bounded_filesystem import (
    existing_file_mode as _existing_file_mode,
)
from lading.commands.bump_toml_bounded_filesystem import (
    open_workspace_parent_directory as _open_workspace_parent_directory,
)
from lading.commands.bump_toml_bounded_filesystem import (
    set_file_mode as _set_file_mode,
)
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
    expected_current_text: str | None = None,
) -> None:
    """Atomically replace a text file using UTF-8 without newline translation.

    Without ``workspace_root``, symbolic-link paths are resolved before
    replacement, so the link remains in place and its target receives the new
    content. Existing permission bits are copied to the replacement. When a
    workspace root is supplied, the destination must still resolve to its
    planned path inside that workspace. Platforms with directory-relative
    operations replace relative to a pinned, no-follow directory descriptor.
    Other platforms revalidate the resolved destination immediately before the
    path-based atomic replacement. When ``expected_current_text`` is provided,
    replacement proceeds only if the current UTF-8 text still equals that
    value. Workspace-bounded writes serialize with other Lading writes through
    an advisory lock; other tools can ignore that lock and race the final check.

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
    expected_current_text : str or None, optional
        Exact text expected immediately before replacement. Requires a workspace
        root so comparison and replacement can run under its Lading write lock.
        Defaults to ``None``.

    Raises
    ------
    OSError
        If resolving the destination, creating or writing the temporary file,
        preserving permissions, replacing the destination, or validating a
        bounded destination fails. Bounded writes use descriptor-relative
        replacement where the operating system supports it and path-based
        replacement with final containment validation otherwise.
    ValueError
        If ``expected_current_text`` is provided without ``workspace_root``.

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
    if expected_current_text is not None and workspace_root is None:
        message = "expected_current_text requires workspace_root"
        raise ValueError(message)
    if workspace_root is None:
        if file_path.is_symlink():
            file_path = file_path.resolve(strict=True)
        _write_path_based_atomic_text(file_path, content, None, expected_current_text)
        return

    with bump_workspace_write_lock.workspace_write_lock(workspace_root):
        bounded_workspace_root = workspace_root.resolve()
        file_path = _resolve_bounded_write_target(file_path, bounded_workspace_root)
        if _supports_bounded_directory_operations():
            _write_bounded_atomic_text(
                file_path,
                content,
                bounded_workspace_root,
                expected_current_text,
            )
            return
        _write_path_based_atomic_text(
            file_path,
            content,
            bounded_workspace_root,
            expected_current_text,
        )


def _write_path_based_atomic_text(
    file_path: Path,
    content: str,
    workspace_root: Path | None = None,
    expected_current_text: str | None = None,
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
        descriptor, temporary_name, replacement_path = _create_path_based_temporary(
            dirpath,
            file_path.name,
            temporary_directory,
            directory_fd,
        )
        temporary_path = replacement_path
        _write_temporary_content(descriptor, content, existing_mode)
        _replace_path_based_destination(
            replacement_path, file_path, workspace_root, expected_current_text
        )
    except BaseException as error:
        primary_error = error
        if temporary_name is not None and temporary_path is not None:
            bump_toml_temporary_cleanup.cleanup_path_based_temporary_file(
                temporary_name, temporary_path, directory_fd, error
            )
        raise
    finally:
        if directory_fd is not None:
            _close_descriptor(directory_fd, primary_error)


def _create_path_based_temporary(
    directory: Path,
    destination_name: str,
    temporary_directory: Path,
    directory_fd: int | None,
) -> tuple[int, str, Path]:
    """Create a temporary replacement and return its descriptor and path."""
    if directory_fd is None:
        descriptor, temporary_path_value = tempfile.mkstemp(
            dir=directory,
            prefix=".lading-",
            text=True,
        )
        temporary_path = Path(temporary_path_value)
        return descriptor, temporary_path.name, temporary_path
    descriptor, temporary_name = _create_bounded_temporary_file(
        directory_fd, destination_name
    )
    return descriptor, temporary_name, temporary_directory / temporary_name


def _write_temporary_content(
    descriptor: int,
    content: str,
    existing_mode: int | None,
) -> None:
    """Write the replacement text while preserving an existing mode."""
    handle = _open_temporary_text_file(descriptor)
    with handle:
        if existing_mode is not None:
            _set_file_mode(handle.fileno(), existing_mode)
        handle.write(content)


def _replace_path_based_destination(
    temporary_path: Path,
    file_path: Path,
    workspace_root: Path | None,
    expected_current_text: str | None,
) -> None:
    """Validate a bounded destination and atomically replace it."""
    if workspace_root is not None:
        _resolve_bounded_write_target(file_path, workspace_root)
    if expected_current_text is not None:
        _validate_expected_current_text(file_path, expected_current_text)
    temporary_path.replace(file_path)


def _validate_expected_current_text(file_path: Path, expected_text: str) -> None:
    """Reject replacement when the destination changed after it was read."""
    try:
        current_text = file_path.read_text(encoding="utf-8", newline="")
    except FileNotFoundError as exc:
        message = (
            f"Atomic write destination disappeared before replacement: {file_path}"
        )
        raise PermissionError(message) from exc
    if current_text != expected_text:
        message = f"Atomic write destination changed before replacement: {file_path}"
        raise PermissionError(message)


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
    file_path: Path,
    content: str,
    workspace_root: Path,
    expected_current_text: str | None = None,
) -> None:
    """Replace an in-workspace file relative to its opened parent directory."""
    relative_path = file_path.relative_to(workspace_root)
    directory_fd = _open_workspace_parent_directory(
        workspace_root, relative_path.parent
    )
    temporary_name: str | None = None
    primary_error: BaseException | None = None
    try:
        existing_mode = _existing_file_mode(directory_fd, file_path.name)
        temporary_fd, temporary_name = _create_bounded_temporary_file(
            directory_fd, file_path.name
        )
        _write_temporary_content(temporary_fd, content, existing_mode)
        if expected_current_text is not None:
            _validate_expected_bounded_text(
                directory_fd, file_path.name, expected_current_text
            )
        os.rename(
            temporary_name,
            relative_path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            if temporary_name is not None:
                bump_toml_temporary_cleanup.cleanup_bounded_temporary_file(
                    temporary_name, directory_fd, primary_error
                )
        finally:
            _close_descriptor(directory_fd, primary_error)


def _validate_expected_bounded_text(
    directory_fd: int,
    filename: str,
    expected_text: str,
) -> None:
    """Compare exact UTF-8 text through the pinned parent directory handle."""
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
        if handle.read() != expected_text:
            message = f"Atomic write destination changed before replacement: {filename}"
            raise PermissionError(message)


def _supports_bounded_directory_operations() -> bool:
    """Check whether this platform supports the required descriptor APIs."""
    required = (os.open, os.stat, os.unlink, os.rename)
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and all(operation in os.supports_dir_fd for operation in required)
        and os.stat in os.supports_follow_symlinks
    )
