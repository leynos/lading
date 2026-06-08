"""Subprocess-backed implementation of the command runner port."""

from __future__ import annotations

import codecs
import collections.abc as cabc
import dataclasses as dc
import logging
import re
import subprocess
import sys
import threading
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError
from lading.utils.process import format_command, log_command_invocation

_LOGGER = logging.getLogger(__name__)

_ENV_REDACTION_TOKENS = (
    "TOKEN",
    "AUTH",
    "BEARER",
    "PASS",
    "CRED",
    "PASSPHRASE",
    "SECRET",
    "KEY",
)
_THREAD_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_STREAM_CHUNK_SIZE = 4096


class CommandSpawnError(LadingError):
    """Raised when a command cannot be spawned."""

    def __init__(self, program: str, reason: BaseException) -> None:
        """Capture the failed program and underlying spawn failure."""
        self.program = program
        self.reason = reason
        message = f"Failed to execute {program!r}: {reason}"
        super().__init__(message)


@dc.dataclass(frozen=True, slots=True)
class SubprocessContext:
    """Execution context for subprocess invocations."""

    cwd: Path | None = None
    env: cabc.Mapping[str, str] | None = None
    stdin_data: str | None = None
    echo_stdout: bool = True


def subprocess_runner(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, str] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
    """Execute ``command`` in a subprocess.

    Parameters
    ----------
    command:
        Program and arguments to execute.
    cwd:
        Optional working directory for the subprocess.
    env:
        Optional environment mapping for the subprocess.
    echo_stdout:
        Whether stdout should be mirrored while being captured.

    Returns
    -------
    tuple[int, str, str]
        Exit code, captured stdout, and captured stderr.

    Raises
    ------
    ValueError
        If ``command`` is empty.
    CommandSpawnError
        If the program cannot be spawned.

    """
    log_command_invocation(_LOGGER, command, cwd)
    program, args = split_command(command)
    context = SubprocessContext(cwd=cwd, env=env, echo_stdout=echo_stdout)
    return invoke_via_subprocess(program, args, context)


def split_command(command: cabc.Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Return the program and argument tuple for ``command``.

    Parameters
    ----------
    command:
        Program and arguments to split.

    Returns
    -------
    tuple[str, tuple[str, ...]]
        Program name and immutable argument tuple.

    Raises
    ------
    ValueError
        If ``command`` is empty.

    """
    if not command:
        message = "Command sequence must contain at least one entry"
        raise ValueError(message)
    program = command[0]
    args = tuple(command[1:])
    return program, args


def _spawn_process(
    program: str,
    command: tuple[str, ...],
    context: SubprocessContext,
    normalised_env: dict[str, str] | None,
) -> subprocess.Popen[bytes]:
    """Create a ``Popen`` instance, mapping ``OSError`` to ``CommandSpawnError``."""
    try:
        # This path owns `Popen` directly so relay threads can drain both pipes
        # before the function returns. S603 is mitigated because `command` is a
        # pre-split sequence and the default `shell=False` is used.
        return subprocess.Popen(  # noqa: S603 # pylint: disable=consider-using-with
            command,
            cwd=None if context.cwd is None else str(context.cwd),
            env=normalised_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if context.stdin_data is not None else None,
        )
    except OSError as exc:
        raise CommandSpawnError(program, exc) from exc


def _drain_stdin_and_wait(
    process: subprocess.Popen[bytes],
    context: SubprocessContext,
    threads: list[threading.Thread],
) -> int:
    """Feed ``stdin_data`` and wait for ``process`` and ``threads`` to finish."""
    try:
        if context.stdin_data is not None and process.stdin is not None:
            try:
                process.stdin.write(context.stdin_data.encode("utf-8"))
                process.stdin.close()
            except BrokenPipeError:
                _LOGGER.debug("Process closed stdin before all data was written")
    finally:
        exit_code = process.wait()
        for thread in threads:
            thread.join()
    return exit_code


def invoke_via_subprocess(
    program: str,
    args: tuple[str, ...],
    context: SubprocessContext,
) -> tuple[int, str, str]:
    """Spawn ``program`` with ``args`` while proxying its output streams.

    Parameters
    ----------
    program:
        Program to execute.
    args:
        Arguments to pass to ``program``.
    context:
        Working directory, environment, and optional stdin payload.

    Returns
    -------
    tuple[int, str, str]
        Exit code, captured stdout, and captured stderr.

    Raises
    ------
    CommandSpawnError
        If the subprocess cannot be created.

    """
    command = (program, *args)
    _log_subprocess_spawn(command, context.cwd)
    _log_subprocess_environment(context.env)
    normalised_env = normalise_environment(context.env)
    process = _spawn_process(program, command, context, normalised_env)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        threading.Thread(
            target=relay_stream,
            args=(
                process.stdout,
                sys.stdout if context.echo_stdout else None,
                stdout_chunks,
            ),
            name=_format_thread_name(program, "stdout"),
            daemon=True,
        ),
        threading.Thread(
            target=relay_stream,
            args=(process.stderr, sys.stderr, stderr_chunks),
            name=_format_thread_name(program, "stderr"),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    exit_code = _drain_stdin_and_wait(process, context, threads)
    return exit_code, "".join(stdout_chunks), "".join(stderr_chunks)


def normalise_environment(
    env: cabc.Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return ``env`` with stringified values to satisfy ``subprocess``.

    Parameters
    ----------
    env:
        Environment overrides supplied by the caller.

    Returns
    -------
    dict[str, str] | None
        String-only environment mapping, or :data:`None` to inherit the
        process environment.

    Raises
    ------
    TypeError
        If ``env`` cannot be iterated as a mapping.

    """
    if env is None:
        return None
    # Defensive: callers sometimes provide ``Path``/custom types despite the
    # annotated signature; ``subprocess`` insists on ``str`` values.
    return {key: str(value) for key, value in env.items()}


def relay_stream(
    source: typ.IO[bytes] | None,
    sink: typ.TextIO | None,
    buffer: list[str],
) -> None:
    """Forward ``source`` into ``sink`` while preserving captured output.

    Parameters
    ----------
    source:
        Byte stream to read from.
    sink:
        Text stream to mirror decoded output to.
    buffer:
        Mutable list receiving decoded chunks.

    Returns
    -------
    None
        The function mutates ``buffer`` in place.

    Raises
    ------
    OSError
        If reading or closing the source stream fails.
    ValueError
        If a stream operation occurs on a closed stream.

    """
    if source is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    active_sink = sink
    try:
        try:
            while True:
                chunk = source.read(_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    buffer.append(text)
                    active_sink = write_to_sink(active_sink, text)
            tail = decoder.decode(b"", final=True)
            if tail:
                buffer.append(tail)
                active_sink = write_to_sink(active_sink, tail)
        finally:
            source.close()
    except (OSError, ValueError):  # pragma: no cover - defensive logging guard
        # Log first, then re-raise into threading.excepthook; join() may still
        # leave a partial buffer, which is preferable to hiding stream corruption.
        _LOGGER.exception("Stream relay thread failed")
        raise


def write_to_sink(sink: typ.TextIO | None, payload: str) -> typ.TextIO | None:
    """Write ``payload`` to ``sink`` and swallow broken pipes.

    Parameters
    ----------
    sink:
        Text stream to write to, or :data:`None`.
    payload:
        Text to write.

    Returns
    -------
    TextIO | None
        The original sink when it remains usable, otherwise :data:`None`.

    Raises
    ------
    OSError
        If the sink raises an I/O error other than :class:`BrokenPipeError`.

    """
    if sink is None or not payload:
        return sink
    try:
        sink.write(payload)
        sink.flush()
    except BrokenPipeError:
        return None
    return sink


def _format_thread_name(program: str, stream: str) -> str:
    """Return a deterministic, filesystem-safe thread name suffix."""
    base = Path(program).name or program
    safe = _THREAD_NAME_PATTERN.sub("-", base).strip("-") or "command"
    return f"lading-cmd-{safe}-{stream}"


def _log_subprocess_spawn(
    command: cabc.Sequence[str], cwd: Path | None
) -> None:  # pragma: no cover - logging only
    """Log the rendered subprocess command and optional working directory."""
    rendered = format_command(command)
    if cwd is None:
        _LOGGER.debug("Spawning subprocess: %s", rendered)
    else:
        _LOGGER.debug("Spawning subprocess: %s (cwd=%s)", rendered, cwd)


def _log_subprocess_environment(env: cabc.Mapping[str, str] | None) -> None:
    """Log redacted environment overrides for subprocess execution."""
    if not env:
        _LOGGER.debug("Spawning subprocess with inherited environment")
        return
    redacted = _redact_environment(env)
    _LOGGER.debug("Subprocess environment overrides: %s", redacted)


def _redact_environment(env: cabc.Mapping[str, str]) -> dict[str, str]:
    """Return ``env`` with sensitive values replaced by placeholders."""
    redacted: dict[str, str] = {}
    for key, value in env.items():
        redacted[key] = "<redacted>" if _should_redact_env_key(key) else str(value)
    return dict(sorted(redacted.items()))


def _should_redact_env_key(key: str) -> bool:
    """Return True when ``key`` likely contains secret material."""
    upper_key = key.upper()
    return any(token in upper_key for token in _ENV_REDACTION_TOKENS)
