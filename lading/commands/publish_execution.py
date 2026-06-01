"""Command execution helpers for publish operations."""

from __future__ import annotations

import collections.abc as cabc
import importlib
import logging
import typing as typ
from pathlib import Path

from lading.runtime import CommandRunner, CommandSpawnError, SubprocessContext
from lading.runtime.subprocess_runner import split_command as _runtime_split_command
from lading.runtime.subprocess_runner import (
    subprocess_runner as _default_subprocess_runner,
)
from lading.utils.process import log_command_invocation

LOGGER = logging.getLogger(__name__)
_subprocess_helpers = importlib.import_module("lading.runtime.subprocess_runner")

if typ.TYPE_CHECKING:
    from lading.commands.publish import PublishPreflightError

_CommandRunner = CommandRunner
_SubprocessContext = SubprocessContext
_invoke_via_subprocess = _subprocess_helpers.invoke_via_subprocess
_format_thread_name = _subprocess_helpers._format_thread_name
_log_subprocess_environment = _subprocess_helpers._log_subprocess_environment
_normalise_environment = _subprocess_helpers.normalise_environment
_redact_environment = _subprocess_helpers._redact_environment
_relay_stream = _subprocess_helpers.relay_stream
_should_redact_env_key = _subprocess_helpers._should_redact_env_key
_write_to_sink = _subprocess_helpers.write_to_sink


def _echo_buffered_output(payload: str, sink: typ.TextIO) -> None:
    """Emit buffered output when a caller needs deferred stream replay."""
    if not payload:
        return
    _write_to_sink(sink, payload)


def _publish_error(message: str) -> PublishPreflightError:
    """Return a PublishPreflightError instance without creating import cycles."""
    from lading.commands.publish import PublishPreflightError

    return PublishPreflightError(message)


def _invoke(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute ``command`` and return the exit status and decoded streams."""
    log_command_invocation(LOGGER, command, cwd)
    try:
        return _default_subprocess_runner(command, cwd=cwd, env=env)
    except ValueError as exc:
        raise _publish_error(str(exc)) from exc
    except CommandSpawnError as exc:
        raise _publish_error(str(exc)) from exc


def _split_command(command: cabc.Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Return the program and argument tuple for ``command``."""
    try:
        return _runtime_split_command(command)
    except ValueError as exc:
        raise _publish_error(str(exc)) from exc


split_command = _split_command

__all__ = [
    "_CommandRunner",
    "_invoke",
    "split_command",
]
