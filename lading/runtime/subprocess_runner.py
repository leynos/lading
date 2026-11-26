"""Subprocess-backed implementation of the command runner port."""

import collections.abc as cabc
import dataclasses as dc
import logging
import subprocess
import sys
import threading
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError
from lading.utils.process import c_locale_env, log_command_invocation
from lading.utils.redaction import redact_text

from .env_redaction import log_subprocess_environment
from .stream_relay import RelayCapture, relay_decoded
from .stream_relay import TextSink as _TextSink
from .stream_relay import format_thread_name as _format_thread_name
from .stream_relay import write_to_relay_sink as _write_to_relay_sink

if typ.TYPE_CHECKING:
    from .relay_events import StreamName

_LOGGER = logging.getLogger(__name__)


class CommandSpawnError(LadingError):
    """Raised when a command cannot be spawned."""

    def __init__(self, program: str, reason: BaseException) -> None:
        """Capture the failed program and underlying spawn failure."""
        self.program = program
        self.reason = reason
        message = f"Failed to execute {program!r}: {reason}"
        super().__init__(message)


def _spawn_error(
    program: str, reason: OSError, secrets: tuple[str, ...]
) -> CommandSpawnError:
    """Return the spawn failure with ``secrets`` hidden from its text and cause.

    Returns
    -------
    CommandSpawnError
        The original details when nothing is hidden, else redacted ones.
    """
    shown = redact_text(str(reason), secrets), redact_text(program, secrets)
    if shown == (str(reason), program):
        return CommandSpawnError(program, reason)
    return CommandSpawnError(shown[1], OSError(shown[0]))


@dc.dataclass(frozen=True, slots=True)
class SubprocessContext:
    """Execution context for subprocess invocations."""

    cwd: Path | None = None
    env: cabc.Mapping[str, object] | None = None
    stdin_data: str | None = None
    echo_stdout: bool = True
    secrets: tuple[str, ...] = ()


def subprocess_runner(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
    env: cabc.Mapping[str, object] | None = None,
    echo_stdout: bool = True,
) -> tuple[int, str, str]:
    r"""Execute ``command`` in a subprocess.

    Parameters
    ----------
    command : cabc.Sequence[str]
        Program and arguments to execute.
    cwd : Path | None
        Optional working directory for the subprocess.
    env : cabc.Mapping[str, object] | None
        Optional environment mapping. Non-string values (``int``, ``Path``)
        become ``str()``; :func:`lading.utils.process.c_locale_env` then pins
        the C locale so failure classification ignores the operator's language.
    echo_stdout : bool
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

    Examples
    --------
    >>> subprocess_runner(["echo", "hello"])  # doctest: +SKIP
    (0, 'hello\n', '')
    """
    context = SubprocessContext(cwd=cwd, env=env, echo_stdout=echo_stdout)
    return run_logged(command, context)


def run_logged(
    command: cabc.Sequence[str], context: SubprocessContext
) -> tuple[int, str, str]:
    """Log ``command`` as an external invocation, then run it under ``context``.

    This is the one place the ``Running external command`` line is emitted, so
    every runner built on it (including the redacting one) logs identically.

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
    # An argument can carry a credential too, so the logged line is scrubbed.
    shown = [redact_text(argument, context.secrets) for argument in command]
    log_command_invocation(_LOGGER, shown, context.cwd)
    program, args = split_command(command)
    return invoke_via_subprocess(program, args, context)


def split_command(command: cabc.Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Return the program and argument tuple for ``command``.

    Parameters
    ----------
    command : cabc.Sequence[str]
        Program and arguments to split.

    Returns
    -------
    tuple[str, tuple[str, ...]]
        Program name and immutable argument tuple.

    Raises
    ------
    ValueError
        If ``command`` is empty.

    Examples
    --------
    >>> split_command(["git", "status", "--short"])
    ('git', ('status', '--short'))
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
    normalized_env: dict[str, str] | None,
) -> subprocess.Popen[bytes]:
    """Create a ``Popen`` instance, mapping ``OSError`` to ``CommandSpawnError``."""
    try:
        # This path owns `Popen` directly so relay threads can drain both pipes
        # before the function returns. S603 is mitigated because `command` is a
        # pre-split sequence and the default `shell=False` is used.
        return subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] # pylint: disable=consider-using-with
            command,
            cwd=None if context.cwd is None else str(context.cwd),
            env=normalized_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if context.stdin_data is not None else None,
        )
    except OSError as exc:
        error = _spawn_error(program, exc, context.secrets)
        raise error from (exc if error.reason is exc else None)


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


def _relay_thread(
    program: str,
    source: typ.IO[bytes] | None,
    sink: _TextSink | None,
    capture: RelayCapture,
) -> threading.Thread:
    """Return the unstarted thread that relays and captures one stream."""
    return threading.Thread(
        target=relay_decoded,
        args=(source, sink, capture),
        name=_format_thread_name(program, capture.stream),
        daemon=True,
    )


def invoke_via_subprocess(
    program: str,
    args: tuple[str, ...],
    context: SubprocessContext,
) -> tuple[int, str, str]:
    r"""Spawn ``program`` with ``args`` while proxying its output streams.

    Parameters
    ----------
    program : str
        Program to execute.
    args : tuple[str, ...]
        Arguments to pass to ``program``.
    context : SubprocessContext
        Working directory, environment, and optional stdin payload.

    Returns
    -------
    tuple[int, str, str]
        Exit code, captured stdout, and captured stderr.

    Raises
    ------
    CommandSpawnError
        If ``program`` cannot be spawned.

    Examples
    --------
    >>> context = SubprocessContext()
    >>> invoke_via_subprocess("echo", ("hello",), context)  # doctest: +SKIP
    (0, 'hello\n', '')
    """
    command = (program, *args)
    # The command line itself is logged once, at INFO, by
    # ``subprocess_runner`` via ``log_command_invocation``; only the
    # environment overrides are worth an extra DEBUG record here.
    log_subprocess_environment(context.env, context.secrets)
    normalized_env = c_locale_env(normalize_environment(context.env))
    process = _spawn_process(program, command, context, normalized_env)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        _relay_thread(
            program,
            process.stdout,
            sys.stdout if context.echo_stdout else None,
            RelayCapture(stdout_chunks, "stdout", context.secrets),
        ),
        _relay_thread(
            program,
            process.stderr,
            sys.stderr,
            RelayCapture(stderr_chunks, "stderr", context.secrets),
        ),
    ]
    for thread in threads:
        thread.start()
    exit_code = _drain_stdin_and_wait(process, context, threads)
    return exit_code, "".join(stdout_chunks), "".join(stderr_chunks)


def normalize_environment(
    env: cabc.Mapping[str, object] | None,
) -> dict[str, str] | None:
    """Return ``env`` with stringified values to satisfy ``subprocess``.

    Parameters
    ----------
    env : cabc.Mapping[str, object] | None
        Environment overrides supplied by the caller. Non-string values
        (for example ``int`` or ``Path``) are converted with ``str()``.

    Returns
    -------
    dict[str, str] | None
        String-only environment mapping, or :data:`None` to inherit the
        process environment.

    Examples
    --------
    >>> normalize_environment({"PATH": "/usr/bin", "PORT": 8080})
    {'PATH': '/usr/bin', 'PORT': '8080'}
    >>> normalize_environment(None) is None
    True
    """
    if env is None:
        return None
    # Values may be any object (callers pass ``Path``/``int`` freely);
    # ``subprocess`` insists on ``str``, so every value is stringified here.
    return {key: str(value) for key, value in env.items()}


def write_to_sink(
    sink: _TextSink | None,
    payload: str,
    stream: StreamName,
) -> _TextSink | None:
    """Write ``payload`` to ``sink`` without corrupting Unicode output.

    Parameters
    ----------
    sink : _TextSink | None
        Text stream to write to, or :data:`None`.
    payload : str
        Text to write.
    stream : StreamName
        ``"stdout"`` or ``"stderr"`` label for the relayed child stream.

    Returns
    -------
    TextIO | None
        The original sink when it remains usable, otherwise :data:`None`.

    Raises
    ------
    OSError
        If writing to or flushing ``sink`` fails for a reason other than a
        broken pipe.
    ValueError
        If writing to or flushing a closed ``sink`` fails.

    Examples
    --------
    >>> import io
    >>> sink = io.StringIO()
    >>> write_to_sink(sink, "hello", "stdout") is sink
    True
    >>> sink.getvalue()
    'hello'
    """
    active_sink, _binary_sink = _write_to_relay_sink(sink, None, stream, payload)
    return active_sink
