"""Process execution helpers for :mod:`lading`.

This module is the canonical home for the small idioms shared by every command
that shells out to ``cargo`` or ``git`` and must report a failure to operators.
It covers two concerns:

* **Command rendering / logging** — :func:`format_command` and
  :func:`log_command_invocation` produce a stable, shell-style representation of
  a command for log output.
* **Failure-detail formatting** — :func:`command_detail`,
  :func:`append_detail`, and :func:`with_detail` collapse the
  ``(stderr or stdout).strip()`` idiom into one place (issue #102) so every
  call site renders the same operator-facing text.

The failure-detail helpers form a small layer:

* :func:`command_detail` picks the most informative stripped stream.
* :func:`append_detail` joins an *already-derived* detail onto a message; use it
  when the caller has computed the detail itself (for example to branch on its
  content) and must not derive it twice.
* :func:`with_detail` is the convenience wrapper that derives and appends in one
  call.

Dependent modules that render command failures —
:mod:`lading.commands.lockfile`, :mod:`lading.commands.bump_lockfiles`,
:mod:`lading.commands.publish_preflight`,
:mod:`lading.commands.publish_index_check`, and
:mod:`lading.workspace.metadata` — must call these helpers rather than
re-implementing the idiom inline.
"""

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

    Parameters
    ----------
    stdout : str
        Captured standard-output stream of the failed command.
    stderr : str
        Captured standard-error stream of the failed command.

    Returns
    -------
    str
        Stripped ``stderr`` when non-empty, otherwise stripped ``stdout``,
        otherwise the empty string.

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


def append_detail(message: str, detail: str, *, separator: str = ": ") -> str:
    """Append an already-derived ``detail`` to ``message`` when non-empty.

    Use this when the caller has already computed ``detail`` (typically via
    :func:`command_detail`, for example to branch on its content) and wants to
    avoid deriving it twice. :func:`with_detail` is the convenience wrapper that
    derives the detail and appends it in a single call.

    Parameters
    ----------
    message : str
        Base failure message to which the detail is appended.
    detail : str
        Pre-derived detail string; appended verbatim only when non-empty.
    separator : str, optional
        String joining ``message`` and ``detail``. Defaults to ``": "``.

    Returns
    -------
    str
        ``message`` unchanged when ``detail`` is empty, otherwise
        ``message`` joined to ``detail`` by ``separator``.

    Examples
    --------
    >>> append_detail("Build failed", "boom")
    'Build failed: boom'
    >>> append_detail("Build failed", "")
    'Build failed'
    """
    if not detail:
        return message
    return f"{message}{separator}{detail}"


def with_detail(
    message: str,
    stdout: str,
    stderr: str,
    *,
    separator: str = ": ",
) -> str:
    """Append command output detail to ``message`` when any is present.

    Derives the detail with :func:`command_detail` and appends it with
    :func:`append_detail`.

    Parameters
    ----------
    message : str
        Base failure message to which the detail is appended.
    stdout : str
        Captured standard-output stream of the failed command.
    stderr : str
        Captured standard-error stream of the failed command.
    separator : str, optional
        String joining ``message`` and the derived detail. Defaults to
        ``": "``.

    Returns
    -------
    str
        ``message`` unchanged when neither stream yields detail, otherwise
        ``message`` joined to the derived detail by ``separator``.

    Examples
    --------
    >>> with_detail("Build failed", "", "boom")
    'Build failed: boom'
    >>> with_detail("Build failed", "", "")
    'Build failed'
    """
    return append_detail(message, command_detail(stdout, stderr), separator=separator)


__all__ = [
    "append_detail",
    "command_detail",
    "format_command",
    "log_command_invocation",
    "with_detail",
]
