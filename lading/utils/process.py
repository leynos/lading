"""Process execution helpers for :mod:`lading`."""

from __future__ import annotations

import shlex
import typing as typ

if typ.TYPE_CHECKING:
    from logging import Logger as LoggerType
    from pathlib import Path as PathType
else:  # pragma: no cover - type-only imports
    LoggerType = typ.Any
    PathType = typ.Any


def _command_as_tuple(command: typ.Sequence[str]) -> tuple[str, ...]:
    """Return ``command`` as an immutable tuple of strings."""
    return tuple(command)


def format_command(command: typ.Sequence[str]) -> str:
    """Return a shell-style representation of ``command`` for logging."""
    command_tuple = _command_as_tuple(command)
    if not command_tuple:
        return ""
    return shlex.join(command_tuple)


def log_command_invocation(
    logger: LoggerType,
    command: typ.Sequence[str],
    cwd: PathType | None,
) -> None:
    """Log ``command`` with optional ``cwd`` using ``logger``."""
    rendered = format_command(command)
    if cwd is None:
        logger.info("Running external command: %s", rendered)
    else:
        logger.info("Running external command: %s (cwd=%s)", rendered, cwd)


__all__ = ["format_command", "log_command_invocation"]
