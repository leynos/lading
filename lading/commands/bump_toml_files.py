"""Read manifests and atomically write TOML files."""

import os
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
    >>> path = Path(tempfile.mkdtemp()) / "Cargo.toml"
    >>> _ = path.write_text('version = "0.1.0"', encoding="utf-8")
    >>> parse_manifest(path)["version"]
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
    planned path inside that workspace.

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
        beneath this root. Defaults to ``None``.

    Raises
    ------
    OSError
        If resolving the destination, creating or writing the temporary file,
        preserving permissions, replacing the destination, or validating a
        bounded destination fails.

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
    else:
        file_path = _resolve_bounded_write_target(file_path, workspace_root)
    dirpath = file_path.parent
    existing_mode: int | None = None
    with suppress(FileNotFoundError):
        existing_mode = stat.S_IMODE(file_path.stat().st_mode)
    fd, tmp_path = tempfile.mkstemp(
        dir=dirpath,
        prefix=f"{file_path.name}.",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            if existing_mode is not None:
                with suppress(AttributeError):
                    _set_file_mode(handle.fileno(), existing_mode)
            handle.write(content)
        Path(tmp_path).replace(file_path)
    finally:
        with suppress(FileNotFoundError):
            Path(tmp_path).unlink()


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


def _set_file_mode(descriptor: int, mode: int) -> None:
    """Apply existing mode bits to the temporary file descriptor."""
    os.fchmod(descriptor, mode)  # not on Windows
