"""Read manifests and atomically write TOML files."""

import dataclasses as dc
import os
import stat
import tempfile
import typing as typ
from contextlib import suppress
from pathlib import Path

from tomlkit import parse as parse_toml

from lading.commands import (
    bump_toml_bounded_filesystem,
    bump_toml_temporary_cleanup,
    bump_toml_write_options,
    bump_workspace_write_lock,
    manifest_paths,
)
from lading.commands.bump_toml_bounded_filesystem import (
    _create_temporary_file as _create_bounded_temporary_file,
)
from lading.commands.bump_toml_bounded_filesystem import (
    _existing_file_mode,
    _normalize_bounded_write_path,
    _open_workspace_parent_directory,
    _set_file_mode,
    _supports_bounded_directory_operations,
)
from lading.commands.bump_toml_temporary_cleanup import (
    _close_descriptor,
    _open_temporary_text_file,
    _supports_stable_temporary_cleanup,
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


def require_workspace_bounded_write_support() -> None:
    """Fail clearly when safe workspace-bounded writes are unavailable.

    Raises
    ------
    LadingError
        If the platform lacks directory-relative, no-follow operations.

    Examples
    --------
    >>> require_workspace_bounded_write_support()  # doctest: +SKIP
    """
    if not _supports_bounded_directory_operations():
        message = (
            "lading bump cannot write manifests on this platform; workspace-"
            "bounded writes require descriptor-relative no-follow operations"
        )
        raise LadingError(message)


def write_atomic_text(
    file_path: Path,
    content: str,
    write_options: bump_toml_write_options.AtomicWriteOptions | None = None,
) -> None:
    """Atomically replace UTF-8 text while preserving existing permission bits.

    Without ``workspace_root``, a symbolic-link destination is followed. A
    bounded write requires descriptor-relative no-follow operations; the writer
    fails before creating a temporary file if the platform lacks them. Bounded
    writes resolve the destination within the workspace and replace it relative
    to a pinned parent directory. ``expected_current_text`` adds a final exact
    text check. Lading writes share an advisory workspace lock, which external
    tools may ignore.

    Parameters
    ----------
    file_path : Path
        Destination file to replace. Without ``workspace_root``, a symbolic
        link is followed and its target is replaced while the link is preserved.
    content : str
        Text to write to the destination.
    write_options : AtomicWriteOptions or None, optional
        Workspace boundary, expected source text, and optional mutable status
        for the replacement. Bounded replacement requires directory-relative
        operations that pin the parent without following symbolic links.
        Defaults to ``None``.

    Raises
    ------
    OSError
        If resolving the destination, creating or writing the temporary file,
        preserving permissions, replacing the destination, or validating a
        bounded destination fails. This includes platforms without the
        descriptor-relative operations required for bounded replacement.
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
    if write_options is None:
        write_options = bump_toml_write_options.AtomicWriteOptions()
    write_status = write_options.status
    if write_status is not None:
        write_status.replacement_completed = False
        write_status.replacement_identity = None
    workspace_root = write_options.workspace_root
    expected_current_text = write_options.expected_current_text
    expected_current_identity = write_options.expected_current_identity
    if workspace_root is None and expected_current_text is not None:
        message = "expected_current_text requires workspace_root"
        raise ValueError(message)
    if workspace_root is None and expected_current_identity is not None:
        message = "expected_current_identity requires workspace_root"
        raise ValueError(message)
    if workspace_root is None:
        _write_path_based_atomic_text(file_path, content, write_status)
        return
    _write_workspace_bounded_atomic_text(
        file_path, content, workspace_root, write_options
    )


def _write_workspace_bounded_atomic_text(
    file_path: Path,
    content: str,
    workspace_root: Path,
    write_options: bump_toml_write_options.AtomicWriteOptions,
) -> None:
    """Replace an in-workspace file using a pinned directory descriptor."""
    if not _supports_bounded_directory_operations():
        message = (
            "Workspace-bounded atomic writes require descriptor-relative "
            "directory operations"
        )
        raise PermissionError(message)
    bounded_workspace_root = workspace_root.resolve()
    with bump_workspace_write_lock.workspace_write_lock(
        bounded_workspace_root
    ) as workspace_identity:
        file_path = _resolve_bounded_write_target(file_path, bounded_workspace_root)
        _write_bounded_atomic_text(
            file_path,
            content,
            bounded_workspace_root,
            dc.replace(write_options, workspace_identity=workspace_identity),
        )


def _write_path_based_atomic_text(
    file_path: Path,
    content: str,
    write_status: bump_toml_write_options.AtomicWriteStatus | None = None,
) -> None:
    """Replace an unbounded destination through a temporary path."""
    destination = _resolve_unbounded_write_destination(file_path)
    dirpath = destination.parent
    existing_mode: int | None = None
    with suppress(FileNotFoundError):
        existing_mode = stat.S_IMODE(destination.stat().st_mode)

    temporary_directory, directory_fd = _open_temporary_parent_directory(dirpath)
    temporary_name: str | None = None
    temporary_path: Path | None = None
    primary_error: BaseException | None = None
    try:
        descriptor, temporary_name, replacement_path = _create_path_based_temporary(
            dirpath,
            destination.name,
            temporary_directory,
            directory_fd,
        )
        temporary_path = replacement_path
        replacement_identity = _write_temporary_content(
            descriptor, content, existing_mode
        )
        if write_status is not None:
            write_status.replacement_identity = replacement_identity
        replacement_path.replace(destination)
        temporary_name = None
        temporary_path = None
        if write_status is not None:
            write_status.replacement_completed = True
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


def _resolve_unbounded_write_destination(file_path: Path) -> Path:
    """Resolve the parent and final link once before inspecting or replacing."""
    destination = file_path.parent.resolve(strict=True) / file_path.name
    if destination.is_symlink():
        destination = destination.resolve(strict=True)
    return destination


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
) -> bump_toml_write_options.AtomicWriteIdentity:
    """Write text and capture the temporary file identity before closing it."""
    primary_error: BaseException | None = None
    try:
        handle = _open_temporary_text_file(descriptor)
        with handle:
            if existing_mode is not None:
                _set_file_mode(handle.fileno(), existing_mode)
            handle.write(content)
            metadata = os.fstat(handle.fileno())
            identity = bump_toml_write_options.AtomicWriteIdentity(
                metadata.st_dev, metadata.st_ino
            )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _close_descriptor(descriptor, primary_error)
    return identity


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


def _resolve_bounded_write_target(file_path: Path, workspace_root: Path) -> Path:
    """Resolve an in-workspace destination before creating its replacement."""
    planned_path = _normalize_bounded_write_path(file_path, workspace_root)
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
    if resolved_path != planned_path:
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
    write_options: bump_toml_write_options.AtomicWriteOptions,
) -> None:
    """Replace an in-workspace file relative to its opened parent directory."""
    workspace_identity = write_options.workspace_identity
    if workspace_identity is None:
        message = "Workspace-bounded atomic write is missing its lock identity"
        raise PermissionError(message)
    relative_path = file_path.relative_to(workspace_root)
    directory_fd = _open_workspace_parent_directory(
        workspace_root, relative_path.parent, workspace_identity
    )
    temporary_name: str | None = None
    primary_error: BaseException | None = None
    try:
        existing_mode = _existing_file_mode(directory_fd, file_path.name)
        temporary_fd, temporary_name = _create_bounded_temporary_file(
            directory_fd, file_path.name
        )
        replacement_identity = _write_temporary_content(
            temporary_fd, content, existing_mode
        )
        has_expected_current_state = (
            write_options.expected_current_text is not None
            or write_options.expected_current_identity is not None
        )
        if has_expected_current_state:
            bump_toml_bounded_filesystem._validate_expected_bounded_text(
                directory_fd,
                file_path.name,
                write_options.expected_current_text,
                write_options.expected_current_identity,
            )
        if write_options.status is not None:
            # Publish the temporary's identity before rename. Recovery can
            # then recognize a rename that completed just before interruption.
            write_options.status.replacement_identity = replacement_identity
        os.rename(
            temporary_name,
            relative_path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        if write_options.status is not None:
            write_options.status.replacement_completed = True
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
