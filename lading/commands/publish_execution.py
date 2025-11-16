"""Command execution helpers for publish operations."""

from __future__ import annotations

import codecs
import dataclasses as dc
import logging
import os
import re
import subprocess
import sys
import threading
import typing as typ
from pathlib import Path

try:  # pragma: no cover - optional dependency hook
    from cmd_mox import command_runner as cmd_runner_module
except ModuleNotFoundError:  # pragma: no cover - fallback when cmd-mox missing
    cmd_runner_module = None  # type: ignore[assignment]

from lading.utils.process import format_command, log_command_invocation
from lading.workspace import metadata as metadata_module

LOGGER = logging.getLogger("lading.commands.publish")

class CmdMoxModules(typ.NamedTuple):
    """Container for dynamically loaded cmd-mox modules."""

    ipc: object
    env: object
    command_runner: object


_ENV_REDACTION_TOKENS = (
    "TOKEN",
    "AUTH",
    "BEARER",
    "PASS",
    "CRED",
    "PASSPHRASE",
)
_THREAD_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_STREAM_CHUNK_SIZE = 4096

if typ.TYPE_CHECKING:  # pragma: no cover - typing helper
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


@dc.dataclass(frozen=True)
class _SubprocessContext:
    """Execution context for subprocess invocations."""

    cwd: Path | None = None
    env: typ.Mapping[str, str] | None = None
    stdin_data: str | None = None


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
    context = _SubprocessContext(cwd=cwd, env=env)
    return _invoke_via_subprocess(program, args, context)


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


def _prepare_cmd_mox_context() -> tuple[object, object, float]:
    """Return cmd-mox IPC modules and timeout after validating env state."""
    if cmd_runner_module is None:  # pragma: no cover - defensive
        message = "cmd-mox is not available but the stub mode was requested"
        raise _publish_error(message)
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
    return ipc, env_mod, timeout


def _build_cmd_mox_invocation_env(
    cwd: Path | None, env: typ.Mapping[str, str] | None
) -> dict[str, str]:
    """Return the environment mapping for cmd-mox invocations."""
    invocation_env = metadata_module._build_invocation_environment(
        None if cwd is None else str(cwd)
    )
    if env is not None:
        invocation_env.update({key: str(value) for key, value in env.items()})
    return invocation_env


def _process_cmd_mox_response(
    response: object, *, streamed: bool
) -> tuple[int, str, str]:
    """Apply environment updates and return decoded response payloads."""
    _apply_cmd_mox_environment(getattr(response, "env", {}))
    stdout_text = metadata_module._coerce_text(getattr(response, "stdout", ""))
    stderr_text = metadata_module._coerce_text(getattr(response, "stderr", ""))
    if not streamed:
        _echo_buffered_output(stdout_text, sys.stdout)
        _echo_buffered_output(stderr_text, sys.stderr)
    return getattr(response, "exit_code", 0), stdout_text, stderr_text


def _invoke_via_cmd_mox(
    command: typ.Sequence[str],
    cwd: Path | None,
    env: typ.Mapping[str, str] | None,
) -> tuple[int, str, str]:
    """Route ``command`` through the cmd-mox IPC server when enabled."""
    ipc, env_mod, timeout = _prepare_cmd_mox_context()
    program, args = _split_command(command)
    invocation_program, invocation_args = _normalise_cmd_mox_command(program, args)
    invocation_env = _build_cmd_mox_invocation_env(cwd, env)
    invocation = ipc.Invocation(
        command=invocation_program,
        args=invocation_args,
        stdin="",
        env=invocation_env,
    )
    response = ipc.invoke_server(invocation, timeout)
    modules = CmdMoxModules(ipc=ipc, env=env_mod, command_runner=cmd_runner_module)
    response, streamed = _handle_cmd_mox_passthrough(
        response,
        invocation,
        timeout=timeout,
        modules=modules,
    )
    return _process_cmd_mox_response(response, streamed=streamed)


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
    context: _SubprocessContext,
) -> tuple[int, str, str]:
    """Spawn ``program`` with ``args`` while proxying its output streams."""
    command = (program, *args)
    _log_subprocess_spawn(command, context.cwd)
    _log_subprocess_environment(context.env)
    normalised_env = _normalise_environment(context.env)
    try:
        process = subprocess.Popen(  # noqa: S603 - command list is fully controlled
            command,
            cwd=None if context.cwd is None else str(context.cwd),
            env=normalised_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if context.stdin_data is not None else None,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        message = f"Failed to execute {program!r}: {exc}"
        raise _publish_error(message) from exc

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        threading.Thread(
            target=_relay_stream,
            args=(process.stdout, sys.stdout, stdout_chunks),
            name=_format_thread_name(program, "stdout"),
            daemon=True,
        ),
        threading.Thread(
            target=_relay_stream,
            args=(process.stderr, sys.stderr, stderr_chunks),
            name=_format_thread_name(program, "stderr"),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    if context.stdin_data is not None and process.stdin is not None:
        try:
            process.stdin.write(context.stdin_data.encode("utf-8"))
            process.stdin.close()
        except BrokenPipeError:
            pass
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
    # Defensive: callers sometimes provide ``Path``/custom types despite the
    # annotated signature; ``subprocess`` insists on ``str`` values.
    return {key: str(value) for key, value in env.items()}


def _handle_cmd_mox_passthrough(
    response: object,
    invocation: object,
    *,
    timeout: float,
    modules: CmdMoxModules,
) -> tuple[object, bool]:
    """Run passthrough commands locally to preserve streaming semantics."""
    directive = getattr(response, "passthrough", None)
    if directive is None:
        return response, False

    passthrough_env = _build_cmd_mox_passthrough_env(
        directive,
        invocation,
        modules=modules,
    )
    resolved = modules.command_runner.resolve_command_with_override(
        invocation.command,
        passthrough_env.get("PATH", ""),
        os.environ.get(
            f"{modules.env.CMOX_REAL_COMMAND_ENV_PREFIX}{invocation.command}"
        ),
    )
    if isinstance(resolved, modules.ipc.Response):
        passthrough_result = modules.ipc.PassthroughResult(
            invocation_id=directive.invocation_id,
            stdout=resolved.stdout,
            stderr=resolved.stderr,
            exit_code=resolved.exit_code,
        )
        return modules.ipc.report_passthrough_result(passthrough_result, timeout), False

    context = _SubprocessContext(
        cwd=None,
        env=passthrough_env,
        stdin_data=invocation.stdin or None,
    )
    exit_code, stdout, stderr = _invoke_via_subprocess(
        str(resolved),
        tuple(invocation.args),
        context,
    )
    passthrough_result = modules.ipc.PassthroughResult(
        invocation_id=directive.invocation_id,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )
    final_response = modules.ipc.report_passthrough_result(
        passthrough_result, timeout
    )
    return final_response, True


def _build_cmd_mox_passthrough_env(
    directive: object,
    invocation: object,
    *,
    modules: CmdMoxModules,
) -> dict[str, str]:
    """Return the merged environment for cmd-mox passthrough executions."""
    env = modules.command_runner.prepare_environment(
        directive.lookup_path,
        directive.extra_env,
        invocation.env,
    )
    env["PATH"] = _merge_cmd_mox_path_entries(
        env.get("PATH"),
        directive.lookup_path,
        env_module=modules.env,
    )
    return env


def _merge_cmd_mox_path_entries(
    current_path: str | None,
    lookup_path: str,
    *,
    env_module: object,
) -> str:
    """Combine PATH entries while filtering the cmd-mox shim directory."""
    shim_dir = _cmd_mox_shim_directory(env_module)
    merged: list[str] = []
    seen: set[str] = set()

    def _add_entries(raw: str | None) -> None:
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


def _cmd_mox_shim_directory(env_module: object) -> Path | None:
    """Return the shim directory recorded in cmd-mox environment variables."""
    socket_path = os.environ.get(env_module.CMOX_IPC_SOCKET_ENV)
    if not socket_path:
        return None
    return Path(socket_path).parent


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


def _apply_cmd_mox_environment(env: typ.Mapping[str, str] | None) -> None:
    """Merge cmd-mox supplied environment updates into ``os.environ``."""
    if not env:
        return
    os.environ.update({str(key): str(value) for key, value in env.items()})


def _echo_buffered_output(payload: str, sink: typ.TextIO) -> None:
    """Emit buffered cmd-mox output so callers still see command logs."""
    if not payload:
        return
    _write_to_sink(sink, payload)


def _format_thread_name(program: str, stream: str) -> str:
    """Return a deterministic, filesystem-safe thread name suffix."""
    base = Path(program).name or program
    safe = _THREAD_NAME_PATTERN.sub("-", base).strip("-") or "command"
    return f"lading-publish-{safe}-{stream}"


def _log_subprocess_spawn(
    command: typ.Sequence[str], cwd: Path | None
) -> None:  # pragma: no cover - logging only
    rendered = format_command(command)
    if cwd is None:
        LOGGER.info("Spawning subprocess: %s", rendered)
    else:
        LOGGER.info("Spawning subprocess: %s (cwd=%s)", rendered, cwd)


def _log_subprocess_environment(env: typ.Mapping[str, str] | None) -> None:
    """Log redacted environment overrides for subprocess execution."""
    if not env:
        LOGGER.debug("Spawning subprocess with inherited environment")
        return
    redacted = _redact_environment(env)
    LOGGER.debug("Subprocess environment overrides: %s", redacted)


def _redact_environment(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Return ``env`` with sensitive values replaced by placeholders."""
    redacted: dict[str, str] = {}
    for key, value in env.items():
        redacted[key] = "<redacted>" if _should_redact_env_key(key) else str(value)
    return dict(sorted(redacted.items()))


def _should_redact_env_key(key: str) -> bool:
    """Return True when ``key`` likely contains secret material."""
    upper_key = key.upper()
    return any(token in upper_key for token in _ENV_REDACTION_TOKENS)
