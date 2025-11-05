"""Process execution helpers for :mod:`lading`."""

from __future__ import annotations

import logging
import shlex
import typing as typ

if typ.TYPE_CHECKING:
    from logging import Logger as LoggerType
    from pathlib import Path as PathType
else:  # pragma: no cover - type-only imports
    LoggerType = typ.Any
    PathType = typ.Any


_LOGGER = logging.getLogger(__name__)


def format_command(command: typ.Sequence[str]) -> str:
    """Return a shell-style representation of ``command`` for logging."""
    if not command:
        _LOGGER.warning(
            "format_command received an empty command sequence; this is likely a bug."
        )
        return ""
    return shlex.join(command)


def log_command_invocation(
    logger: LoggerType,
    command: typ.Sequence[str],
    cwd: PathType | None,
) -> None:
    """Log ``command`` with optional ``cwd`` using ``logger``."""
    rendered = format_command(command) or "<empty command>"
    if cwd is None:
        logger.info("Running external command: %s", rendered)
    else:
        logger.info("Running external command: %s (cwd=%s)", rendered, cwd)


__all__ = ["format_command", "log_command_invocation"]
