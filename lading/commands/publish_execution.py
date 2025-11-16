"""Command execution helpers for publish operations."""

from __future__ import annotations

import codecs
import logging
import os
import subprocess
import sys
import threading
import typing as typ

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
    return _invoke_via_subprocess(program, args, cwd=cwd, env=env)


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
    stdout_text = metadata_module._coerce_text(response.stdout)
    stderr_text = metadata_module._coerce_text(response.stderr)
    _echo_buffered_output(stdout_text, sys.stdout)
    _echo_buffered_output(stderr_text, sys.stderr)
    return (
        response.exit_code,
        stdout_text,
        stderr_text,
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


def _invoke_via_subprocess(
    program: str,
    args: tuple[str, ...],
    *,
    cwd: Path | None,
    env: typ.Mapping[str, str] | None,
) -> tuple[int, str, str]:
    """Spawn ``program`` with ``args`` while proxying its output streams."""
    command = (program, *args)
    try:
        process = subprocess.Popen(  # noqa: S603 - command list is fully controlled
            command,
            cwd=None if cwd is None else str(cwd),
            env=_normalise_environment(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        message = f"{program} executable not found while running pre-flight checks"
        raise _publish_error(message) from exc

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        threading.Thread(
            target=_relay_stream,
            args=(process.stdout, sys.stdout, stdout_chunks),
            name=f"lading-publish-{program}-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=_relay_stream,
            args=(process.stderr, sys.stderr, stderr_chunks),
            name=f"lading-publish-{program}-stderr",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    exit_code = process.wait()
    for thread in threads:
        thread.join()
    return exit_code, "".join(stdout_chunks), "".join(stderr_chunks)


def _normalise_environment(
    env: typ.Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return ``env`` with stringified values to satisfy ``subprocess``."""
    if env is None:
        return None
    return {key: str(value) for key, value in env.items()}


def _relay_stream(
    source: typ.IO[bytes] | None,
    sink: typ.TextIO | None,
    buffer: list[str],
) -> None:
    """Forward ``source`` into ``sink`` while preserving the captured output."""
    if source is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    active_sink = sink
    try:
        while True:
            chunk = source.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            text = decoder.decode(chunk)
            if text:
                buffer.append(text)
                active_sink = _write_to_sink(active_sink, text)
        tail = decoder.decode(b"", final=True)
        if tail:
            buffer.append(tail)
            active_sink = _write_to_sink(active_sink, tail)
    finally:
        source.close()


def _write_to_sink(sink: typ.TextIO | None, payload: str) -> typ.TextIO | None:
    """Write ``payload`` to ``sink`` and swallow broken pipes."""
    if sink is None or not payload:
        return sink
    try:
        sink.write(payload)
        sink.flush()
    except BrokenPipeError:
        return None
    return sink


def _echo_buffered_output(payload: str, sink: typ.TextIO) -> None:
    """Emit buffered cmd-mox output so callers still see command logs."""
    if not payload:
        return
    _write_to_sink(sink, payload)


_STREAM_CHUNK_SIZE = 4096
