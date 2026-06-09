"""Process execution helpers for :mod:`lading`."""

from __future__ import annotations

import collections.abc as cabc
import logging
import shlex
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from logging import Logger as LoggerType
    from pathlib import Path as PathType
else:  # pragma: no cover - type-only imports
    LoggerType = typ.Any
    PathType = typ.Any


_LOGGER = logging.getLogger(__name__)


def format_command(command: cabc.Sequence[str]) -> str:
    """Return a shell-style representation of ``command`` for logging."""
    if not command:
        _LOGGER.warning(
            "format_command received an empty command sequence; this is likely a bug."
        )
        return ""
    return shlex.join(command)


def log_command_invocation(
    logger: LoggerType,
    command: cabc.Sequence[str],
    cwd: PathType | None,
) -> None:
    """Log ``command`` with optional ``cwd`` using ``logger``."""
    rendered = format_command(command) or "<empty command>"
    if cwd is None:
        logger.info("Running external command: %s", rendered)
    else:
        logger.info("Running external command: %s (cwd=%s)", rendered, cwd)


def command_detail(stdout: str, stderr: str) -> str:
    """Return the most informative stripped output stream for a failure.

    ``stderr`` is preferred; ``stdout`` is the fallback when stderr strips to
    nothing. This is the canonical home for the idiom (issue #102) — call
    sites must not re-implement it.

    Examples
    --------
    >>> command_detail("out", "err")
    'err'
    >>> command_detail("out", "  ")
    'out'
    >>> command_detail("", "")
    ''
    """
    return stderr.strip() or stdout.strip()


def with_detail(
    message: str,
    stdout: str,
    stderr: str,
    *,
    separator: str = ": ",
) -> str:
    """Append command output detail to ``message`` when any is present.

    Examples
    --------
    >>> with_detail("Build failed", "", "boom")
    'Build failed: boom'
    >>> with_detail("Build failed", "", "")
    'Build failed'
    """
    detail = command_detail(stdout, stderr)
    if not detail:
        return message
    return f"{message}{separator}{detail}"


__all__ = [
    "command_detail",
    "format_command",
    "log_command_invocation",
    "with_detail",
]
