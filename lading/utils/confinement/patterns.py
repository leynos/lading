"""Lexical validation of workspace-relative patterns.

Patterns arrive from configuration (`bump.documentation.globs`,
`bump.lockfile_manifests`) and from a workspace manifest (`[workspace].members`,
`[workspace].exclude`). They are attacker-influenced in the sense that a typo or
a copied snippet can name a path outside the workspace, so they are validated
lexically before the filesystem is consulted.
"""

import os
import re
import typing as typ
from pathlib import PurePosixPath, PureWindowsPath

from lading.utils.confinement.errors import UnsafeBumpTargetError

__all__ = ["validate_relative_pattern"]

_WINDOWS_DRIVE_QUALIFIER: typ.Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")


def validate_relative_pattern(pattern: str, *, key: str) -> tuple[str, ...]:
    """Validate a workspace-relative pattern and return its components.

    Parameters
    ----------
    pattern : str
        A member, exclusion, or documentation pattern from configuration or a
        workspace manifest. Patterns are not tilde-expanded.
    key : str
        Configuration key or manifest field naming the pattern in diagnostics.

    Returns
    -------
    tuple[str, ...]
        POSIX path components below the workspace root.

    Raises
    ------
    UnsafeBumpTargetError
        If the pattern is empty, contains a NUL byte, is absolute, rooted,
        drive-qualified, UNC, or contains a parent-traversing ``..`` component
        under either POSIX or Windows spelling.

    Examples
    --------
    >>> validate_relative_pattern("crates/*", key="workspace.members")
    ('crates', '*')
    """
    _reject_missing_value(pattern, key)
    _reject_rooted_pattern(pattern, key)
    _reject_parent_traversal(pattern, key)
    parts = _native_parts(pattern)
    if os.sep == "\\":
        for part in parts:
            component = PureWindowsPath(part)
            if component.drive or component.root:
                message = (
                    f"{key}: pattern component must not carry its own drive "
                    f"or root: {part!r}"
                )
                raise UnsafeBumpTargetError(message)
    if not parts:
        message = (
            f"{key}: pattern must name a path below the workspace root: {pattern!r}"
        )
        raise UnsafeBumpTargetError(message)
    return parts


def _native_parts(pattern: str) -> tuple[str, ...]:
    r"""Return pattern components using the host path flavour.

    A backslash is a separator on Windows but a literal filename character on
    POSIX. Using the native flavour after lexical validation means a Windows
    walk inspects every component of ``docs\guide.md`` while a POSIX walk
    keeps the backslash as part of one filename.

    Returns
    -------
    tuple[str, ...]
        Native path components below the workspace root.
    """
    if os.sep == "\\":
        return PureWindowsPath(pattern).parts
    return PurePosixPath(pattern).parts


def _reject_missing_value(pattern: str, key: str) -> None:
    """Reject an empty or NUL-containing pattern."""
    if not pattern.strip():
        message = f"{key}: pattern must not be empty"
        raise UnsafeBumpTargetError(message)
    if "\x00" in pattern:
        message = f"{key}: pattern contains a NUL byte: {pattern!r}"
        raise UnsafeBumpTargetError(message)


def _reject_rooted_pattern(pattern: str, key: str) -> None:
    """Reject absolute, rooted, drive-qualified, and UNC patterns."""
    posix_path = PurePosixPath(pattern)
    windows_path = PureWindowsPath(pattern)
    # ``PureWindowsPath.drive`` covers both ``C:`` and the UNC form
    # ``\\\\server\\share``; ``.root`` covers a leading separator with no drive.
    is_rooted = (
        posix_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
        or bool(_WINDOWS_DRIVE_QUALIFIER.match(pattern))
    )
    if is_rooted:
        message = (
            f"{key}: absolute, rooted, drive-qualified, and UNC patterns are "
            f"not permitted: {pattern!r}"
        )
        raise UnsafeBumpTargetError(message)


def _reject_parent_traversal(pattern: str, key: str) -> None:
    """Reject ``..`` components under either POSIX or Windows spelling."""
    if ".." in PureWindowsPath(pattern).parts:
        message = (
            f"{key}: parent-traversing '..' components are not permitted: {pattern!r}"
        )
        raise UnsafeBumpTargetError(message)
