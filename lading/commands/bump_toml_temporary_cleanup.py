"""Remove failed atomic-write temporaries without hiding write errors."""

import os
import stat
from pathlib import Path


def cleanup_path_based_temporary_file(
    temporary_name: str,
    temporary_path: Path,
    directory_fd: int | None,
    primary_error: BaseException,
) -> None:
    """Remove a failed replacement without hiding its write error.

    Parameters
    ----------
    temporary_name : str
        Temporary entry name, used for descriptor-relative unlinking.
    temporary_path : Path
        Path-based fallback used when no parent descriptor is available.
    directory_fd : int or None
        Open parent descriptor, when available. The caller retains ownership
        and must close it.
    primary_error : BaseException
        Write or replacement error to annotate if cleanup also fails.

    Notes
    -----
    Missing temporary files are already cleaned up. Other cleanup failures are
    added as notes to ``primary_error`` and are not raised separately.
    """
    try:
        if directory_fd is None:
            _unlink_readonly_temporary(temporary_path)
        else:
            os.unlink(temporary_name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError as cleanup_error:
        primary_error.add_note(
            f"Could not remove temporary file {temporary_name!r}: {cleanup_error}"
        )


def _unlink_readonly_temporary(temporary_path: Path) -> None:
    """Retry unlink after clearing a temporary file's read-only flag."""
    try:
        temporary_path.unlink()
    except FileNotFoundError:
        return
    except OSError as first_cleanup_error:
        try:
            temporary_path.chmod(stat.S_IREAD | stat.S_IWRITE)
            temporary_path.unlink()
        except FileNotFoundError:
            return
        except OSError as retry_error:
            message = f"{first_cleanup_error}; writable retry failed: {retry_error}"
            raise OSError(message) from retry_error


def cleanup_bounded_temporary_file(
    temporary_name: str,
    directory_fd: int,
    primary_error: BaseException | None,
) -> None:
    """Remove a descriptor-relative temporary without masking write errors.

    Parameters
    ----------
    temporary_name : str
        Temporary entry name relative to ``directory_fd``.
    directory_fd : int
        Open parent directory descriptor. The caller retains ownership and must
        close it.
    primary_error : BaseException or None
        Write error to annotate if cleanup also fails; ``None`` means cleanup
        errors are raised directly.

    Raises
    ------
    OSError
        If unlinking fails and there is no primary write error to annotate.

    Notes
    -----
    A missing temporary entry is treated as already cleaned up. When a primary
    error exists, other cleanup failures are attached to it as notes.
    """
    try:
        os.unlink(temporary_name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError as cleanup_error:
        if primary_error is None:
            raise
        primary_error.add_note(
            f"Could not remove temporary file {temporary_name!r}: {cleanup_error}"
        )
