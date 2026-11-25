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
    """Remove a failed replacement, retrying read-only path cleanup."""
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
    """Remove a descriptor-relative temporary without masking write errors."""
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
