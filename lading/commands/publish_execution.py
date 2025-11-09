"""Command execution helpers for publish operations."""

from __future__ import annotations

import logging
import os
import typing as typ

from plumbum import local
from plumbum.commands.processes import CommandNotFound

from lading.utils.process import log_command_invocation
from lading.workspace import metadata as metadata_module

LOGGER = logging.getLogger(__name__)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helper
    from pathlib import Path

    from lading.commands.publish import PublishPreflightError


class _CommandRunner(typ.Protocol):
    """Protocol describing the callable used to execute shell commands."""

    def __call__(
        self,
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Execute ``command`` and return exit status and decoded output."""


def _publish_error(message: str) -> PublishPreflightError:
    """Return a PublishPreflightError instance without creating import cycles."""
    from lading.commands.publish import PublishPreflightError

    return PublishPreflightError(message)


def _invoke(
    command: typ.Sequence[str],
    *,
    cwd: Path | None = None,
    env: typ.Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute ``command`` and return the exit status and decoded streams."""
    log_command_invocation(LOGGER, command, cwd)
    if _should_use_cmd_mox_stub():
        return _invoke_via_cmd_mox(command, cwd, env)

    program, args = _split_command(command)
    try:
        bound = local[program]
    except CommandNotFound as exc:
        message = f"{program} executable not found while running pre-flight checks"
        raise _publish_error(message) from exc
    if args:
        bound = bound[list(args)]
    kwargs: dict[str, typ.Any] = {"retcode": None}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if env is not None:
        kwargs["env"] = {key: str(value) for key, value in env.items()}
    exit_code, stdout, stderr = bound.run(**kwargs)
    return (
        exit_code,
        metadata_module._coerce_text(stdout),
        metadata_module._coerce_text(stderr),
    )


def _split_command(command: typ.Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Return the program and argument tuple for ``command``."""
    if not command:
        message = "Command sequence must contain at least one entry"
        raise _publish_error(message)
    program = command[0]
    args = tuple(command[1:])
    return program, args


def _should_use_cmd_mox_stub() -> bool:
    """Return ``True`` when publish invocations should use cmd-mox."""
    stub_env_val = os.environ.get(metadata_module.CMD_MOX_STUB_ENV_VAR, "")
    return stub_env_val.lower() in {"1", "true", "yes", "on"}


def _invoke_via_cmd_mox(
    command: typ.Sequence[str],
    cwd: Path | None,
    env: typ.Mapping[str, str] | None,
) -> tuple[int, str, str]:
    """Route ``command`` through the cmd-mox IPC server when enabled."""
    try:
        ipc, env_mod = metadata_module._load_cmd_mox_modules()
        timeout = metadata_module._resolve_cmd_mox_timeout(
            os.environ.get(env_mod.CMOX_IPC_TIMEOUT_ENV)
        )
    except metadata_module.CargoMetadataError as exc:  # pragma: no cover - defensive
        raise _publish_error(str(exc)) from exc
    if not os.environ.get(env_mod.CMOX_IPC_SOCKET_ENV):
        message = (
            "cmd-mox stub requested for publish pre-flight but CMOX_IPC_SOCKET is unset"
        )
        raise _publish_error(message)
    program, args = _split_command(command)
    invocation_program, invocation_args = _normalise_cmd_mox_command(program, args)
    invocation_env = metadata_module._build_invocation_environment(
        None if cwd is None else str(cwd)
    )
    if env is not None:
        invocation_env.update({key: str(value) for key, value in env.items()})
    invocation = ipc.Invocation(
        command=invocation_program,
        args=invocation_args,
        stdin="",
        env=invocation_env,
    )
    response = ipc.invoke_server(invocation, timeout)
    return (
        response.exit_code,
        metadata_module._coerce_text(response.stdout),
        metadata_module._coerce_text(response.stderr),
    )


def _normalise_cmd_mox_command(
    program: str,
    args: tuple[str, ...],
) -> tuple[str, list[str]]:
    """Return the command name and argument list for cmd-mox invocations."""
    invocation_program = program
    invocation_args = list(args)
    if program == "cargo" and args:
        invocation_program = f"{program}::{args[0]}"
        invocation_args = list(args[1:])
    return invocation_program, invocation_args
