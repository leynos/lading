"""Command execution helpers for publish operations."""

from __future__ import annotations

import collections.abc as cabc
from pathlib import Path

from lading.commands.publish_errors import PublishPreflightError
from lading.runtime import CommandSpawnError
from lading.runtime.subprocess_runner import (
    subprocess_runner as _default_subprocess_runner,
)


def _publish_error(message: str) -> PublishPreflightError:
    """Return a PublishPreflightError instance.

    Returns
    -------
    PublishPreflightError
        The error carrying ``message``.
    """
    return PublishPreflightError(message)


def _invoke(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
    """Execute ``command`` and return the exit status and decoded streams.

    Returns
    -------
    tuple[int, str, str]
        The exit code, decoded standard output, and decoded standard error.

    Raises
    ------
    _publish_error
        A :class:`PublishPreflightError` when the command cannot be spawned or
        its arguments are invalid.
    """
    try:
        return _default_subprocess_runner(
            command, cwd=cwd, env=env, echo_stdout=echo_stdout
        )
    except ValueError as exc:
        raise _publish_error(str(exc)) from exc
    except CommandSpawnError as exc:
        raise _publish_error(str(exc)) from exc


__all__ = [
    "_invoke",
]
