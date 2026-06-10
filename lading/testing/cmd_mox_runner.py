"""cmd-mox-backed implementation of the command runner port."""

from __future__ import annotations

import collections.abc as cabc
import logging
import os
import sys
import typing as typ
from pathlib import Path

from cmd_mox import command_runner, ipc
from cmd_mox import environment as env_mod

from lading.runtime import LadingError, SubprocessContext, coerce_text
from lading.runtime.subprocess_runner import (
    invoke_via_subprocess,
    split_command,
    write_to_sink,
)
from lading.utils.process import log_command_invocation

_LOGGER = logging.getLogger(__name__)
_CMD_MOX_TIMEOUT_DEFAULT = 5.0


class CmdMoxError(LadingError):
    """Raised when the cmd-mox command runner cannot complete an invocation."""


class _PassthroughDirective(typ.Protocol):
    """Subset of cmd-mox passthrough directive fields consumed here."""

    invocation_id: str
    lookup_path: str
    extra_env: cabc.Mapping[str, str] | None


def cmd_mox_runner(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
    """Route ``command`` through cmd-mox's IPC server.

    Parameters
    ----------
    command:
        Command vector to invoke through cmd-mox.
    cwd:
        Working directory to expose to the invocation via ``PWD``.
    env:
        Environment overrides to merge into the invocation environment.

    Returns
    -------
    tuple[int, str, str]
        Exit code, stdout text, and stderr text returned by cmd-mox or a
        passthrough subprocess.

    Raises
    ------
    CmdMoxError
        If the cmd-mox environment is invalid or a passthrough command cannot
        be resolved.
    ValueError
        If the command vector is empty or the cmd-mox response is malformed.
    OSError
        If a passthrough subprocess cannot complete its local invocation.

    Notes
    -----
    Timeout validation, command splitting, command normalisation, environment
    construction, IPC invocation, passthrough handling, and response processing
    are delegated to the focused helpers in this module.
    """
    timeout = _prepare_cmd_mox_context()
    program, args = split_command(command)
    invocation_program, invocation_args = normalise_cmd_mox_command(program, args)
    invocation = ipc.Invocation(
        command=invocation_program,
        args=invocation_args,
        stdin="",
        env=_build_cmd_mox_invocation_env(cwd, env),
    )
    response = ipc.invoke_server(invocation, timeout)
    response, streamed = _handle_cmd_mox_passthrough(
        response,
        invocation,
        timeout=timeout,
    )
    return _process_cmd_mox_response(
        response, streamed=streamed, echo_stdout=echo_stdout
    )


def _prepare_cmd_mox_context() -> float:
    """Return cmd-mox IPC timeout after validating environment state."""
    if not os.environ.get(env_mod.CMOX_IPC_SOCKET_ENV):
        message = "cmd-mox stub requested but CMOX_IPC_SOCKET is unset"
        raise CmdMoxError(message)
    return _resolve_cmd_mox_timeout(os.environ.get(env_mod.CMOX_IPC_TIMEOUT_ENV))


def _resolve_cmd_mox_timeout(raw_timeout: str | None) -> float:
    """Return the IPC timeout to use when contacting cmd-mox."""
    if raw_timeout is None:
        return _CMD_MOX_TIMEOUT_DEFAULT
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        message = "Invalid CMOX_IPC_TIMEOUT value"
        raise CmdMoxError(message) from exc
    if timeout <= 0:
        message = "CMOX_IPC_TIMEOUT must be positive"
        raise CmdMoxError(message)
    return timeout


def _build_cmd_mox_invocation_env(
    cwd: Path | None, env: cabc.Mapping[str, str] | None
) -> dict[str, str]:
    """Return the environment mapping for cmd-mox invocations."""
    invocation_env = dict(os.environ)
    if env is not None:
        invocation_env.update({key: str(value) for key, value in env.items()})
    if cwd is not None:
        invocation_env["PWD"] = str(cwd)
    return invocation_env


def _process_cmd_mox_response(
    response: object, *, streamed: bool, echo_stdout: bool = True
) -> tuple[int, str, str]:
    """Apply environment updates and return decoded response payloads."""
    _apply_cmd_mox_environment(getattr(response, "env", {}))
    stdout_text = coerce_text(getattr(response, "stdout", ""))
    stderr_text = coerce_text(getattr(response, "stderr", ""))
    if not streamed:
        if echo_stdout:
            _echo_buffered_output(stdout_text, sys.stdout)
        _echo_buffered_output(stderr_text, sys.stderr)
    exit_code = getattr(response, "exit_code", None)
    if exit_code is None:
        message = "cmd-mox response did not include an exit code"
        raise ValueError(message)
    return exit_code, stdout_text, stderr_text


def normalise_cmd_mox_command(
    program: str,
    args: tuple[str, ...],
) -> tuple[str, list[str]]:
    """Return the command name and argument list for cmd-mox invocations.

    Parameters
    ----------
    program:
        Executable name from the original command vector.
    args:
        Positional arguments from the original command vector.

    Returns
    -------
    tuple[str, list[str]]
        The ``invocation_program`` and ``invocation_args`` to send to cmd-mox.

    Notes
    -----
    When :func:`_should_namespace_cargo_command` is true, cargo subcommands are
    namespaced for cmd-mox expectations by changing ``program`` and ``args`` to
    ``f"{program}::{args[0]}"`` and ``list(args[1:])``. Cargo metadata remains
    unnamespaced because existing fixtures match it as ``cargo metadata``.
    """
    invocation_program = program
    invocation_args = list(args)
    if _should_namespace_cargo_command(program, args):
        invocation_program = f"{program}::{args[0]}"
        invocation_args = list(args[1:])
    return invocation_program, invocation_args


def _should_namespace_cargo_command(program: str, args: tuple[str, ...]) -> bool:
    """Return True when cmd-mox expectations use cargo subcommand names."""
    if program != "cargo" or not args:
        return False
    return args[0] != "metadata"


def _handle_cmd_mox_passthrough(
    response: object,
    invocation: ipc.Invocation,
    *,
    timeout: float,
) -> tuple[object, bool]:
    """Run passthrough commands locally to preserve streaming semantics."""
    directive = getattr(response, "passthrough", None)
    if directive is None:
        return response, False

    passthrough_env = _build_cmd_mox_passthrough_env(directive, invocation)
    resolved = command_runner.resolve_command_with_override(
        invocation.command,
        passthrough_env.get("PATH", ""),
        os.environ.get(f"{env_mod.CMOX_REAL_COMMAND_ENV_PREFIX}{invocation.command}"),
    )
    if isinstance(resolved, ipc.Response):
        passthrough_result = ipc.PassthroughResult(
            invocation_id=directive.invocation_id,
            stdout=resolved.stdout,
            stderr=resolved.stderr,
            exit_code=resolved.exit_code,
        )
        return ipc.report_passthrough_result(passthrough_result, timeout), False

    cwd_value = passthrough_env.get("PWD")
    cwd = None if not cwd_value else Path(cwd_value)
    context = SubprocessContext(
        cwd=cwd,
        env=passthrough_env,
        stdin_data=invocation.stdin or None,
    )
    passthrough_command = (str(resolved), *invocation.args)
    # This passthrough path calls ``invoke_via_subprocess`` directly rather than
    # going through ``subprocess_runner``, so the single INFO invocation record
    # must be emitted here; otherwise these external commands log nothing.
    log_command_invocation(_LOGGER, passthrough_command, cwd)
    exit_code, stdout, stderr = invoke_via_subprocess(
        str(resolved),
        tuple(invocation.args),
        context,
    )
    passthrough_result = ipc.PassthroughResult(
        invocation_id=directive.invocation_id,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )
    final_response = ipc.report_passthrough_result(passthrough_result, timeout)
    return final_response, True


def _build_cmd_mox_passthrough_env(
    directive: _PassthroughDirective,
    invocation: ipc.Invocation,
) -> dict[str, str]:
    """Return the merged environment for cmd-mox passthrough executions."""
    env = command_runner.prepare_environment(
        directive.lookup_path,
        dict(directive.extra_env or {}),
        dict(invocation.env),
    )
    env["PATH"] = _merge_cmd_mox_path_entries(
        env.get("PATH"),
        directive.lookup_path,
    )
    return env


def _merge_cmd_mox_path_entries(
    current_path: str | None,
    lookup_path: str,
) -> str:
    """Combine PATH entries while filtering the cmd-mox shim directory."""
    shim_dir = _cmd_mox_shim_directory()
    merged: list[str] = []
    seen: set[str] = set()

    def _add_entries(raw: str | None) -> None:
        """Append unseen non-shim PATH entries to the merged path."""
        if not raw:
            return
        for entry in raw.split(os.pathsep):
            candidate = entry.strip()
            if not candidate:
                continue
            if shim_dir is not None and Path(candidate) == shim_dir:
                continue
            if candidate in seen:
                continue
            merged.append(candidate)
            seen.add(candidate)

    _add_entries(current_path)
    _add_entries(lookup_path)
    return os.pathsep.join(merged)


def _cmd_mox_shim_directory() -> Path | None:
    """Return the shim directory recorded in cmd-mox environment variables."""
    socket_path = os.environ.get(env_mod.CMOX_IPC_SOCKET_ENV)
    if not socket_path:
        return None
    return Path(socket_path).parent


def _apply_cmd_mox_environment(env: cabc.Mapping[str, str] | None) -> None:
    """Merge cmd-mox supplied environment updates into ``os.environ``."""
    if not env:
        return
    os.environ.update({str(key): str(value) for key, value in env.items()})


def _echo_buffered_output(payload: str, sink: typ.TextIO) -> None:
    """Emit buffered cmd-mox output so callers still see command logs."""
    if not payload:
        return
    write_to_sink(sink, payload)
