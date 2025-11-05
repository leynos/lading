#!/usr/bin/env -S uv run python
"""Automated publish-check workflow for Rust workspace crates.

This module implements the publish-check automation that validates crate
packaging and compilation in an isolated workspace. The workflow exports
the repository to a temporary directory, strips patch sections, applies
version replacements, and validates each publishable crate.

The script supports timeout configuration via PUBLISH_CHECK_TIMEOUT_SECS
and workspace preservation via PUBLISH_CHECK_KEEP_TMP for debugging.

Examples
--------
Run the complete publish-check workflow::

    python -m crate_tools.run_publish_check

Run with custom timeout and workspace preservation::

    PUBLISH_CHECK_TIMEOUT_SECS=1200 PUBLISH_CHECK_KEEP_TMP=1 \
        python -m crate_tools.run_publish_check

"""

# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "cyclopts>=2.9",
#     "plumbum",
#     "tomlkit",
# ]
# ///
from __future__ import annotations

import codecs
import dataclasses as dc
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import typing as typ
from contextlib import ExitStack, suppress
from pathlib import Path
from types import MappingProxyType

import cyclopts
from cyclopts import App, Parameter
from plumbum import local
from plumbum.commands.processes import ProcessTimedOut

if typ.TYPE_CHECKING:
    from crate_tools.publish_workspace import (
        PUBLISHABLE_CRATES,
        apply_workspace_replacements,
        export_workspace,
        prune_workspace_members,
        remove_patch_entry,
        strip_patch_section,
        workspace_version,
    )
elif __package__ in {None, ""}:
    from publish_workspace import (
        PUBLISHABLE_CRATES,
        apply_workspace_replacements,
        export_workspace,
        prune_workspace_members,
        remove_patch_entry,
        strip_patch_section,
        workspace_version,
    )
else:
    from .publish_workspace import (
        PUBLISHABLE_CRATES,
        apply_workspace_replacements,
        export_workspace,
        prune_workspace_members,
        remove_patch_entry,
        strip_patch_section,
        workspace_version,
    )

LOGGER = logging.getLogger(__name__)

Command = tuple[str, ...]


class CrateAction(typ.Protocol):
    """Protocol describing callable crate actions used by workflow helpers."""

    def __call__(self, crate: str, workspace: Path, *, timeout_secs: int) -> None:
        """Execute the action for ``crate`` within ``workspace``."""
        ...


CRATE_ORDER: typ.Final[tuple[str, ...]] = tuple(PUBLISHABLE_CRATES)

LIVE_PUBLISH_COMMANDS_SEQUENCE: typ.Final[tuple[Command, ...]] = (
    ("cargo", "publish", "--dry-run"),
    ("cargo", "publish"),
)

LIVE_PUBLISH_COMMANDS: typ.Final[typ.Mapping[str, tuple[Command, ...]]]
LIVE_PUBLISH_COMMANDS = MappingProxyType(
    dict.fromkeys(PUBLISHABLE_CRATES, LIVE_PUBLISH_COMMANDS_SEQUENCE)
)

ALREADY_PUBLISHED_MARKERS: typ.Final[tuple[str, ...]] = (
    "already exists on crates.io index",
    "already exists on crates.io",
    "already uploaded",
    "already exists",
)
ALREADY_PUBLISHED_MARKERS_FOLDED: typ.Final[tuple[str, ...]] = tuple(
    marker.casefold() for marker in ALREADY_PUBLISHED_MARKERS
)

DEFAULT_PUBLISH_TIMEOUT_SECS = 900

app = App()
app.config = (cyclopts.config.Env("PUBLISH_CHECK_", command=False),)


def _resolve_timeout(timeout_secs: int | None) -> int:
    """Return the timeout for Cargo commands.

    The value prioritises the explicit ``timeout_secs`` argument. When that is
    omitted, the ``PUBLISH_CHECK_TIMEOUT_SECS`` environment variable is
    consulted to preserve compatibility with the previous helper API before
    falling back to :data:`DEFAULT_PUBLISH_TIMEOUT_SECS`.
    """
    if timeout_secs is not None:
        return timeout_secs

    env_value = os.environ.get("PUBLISH_CHECK_TIMEOUT_SECS")
    if env_value is None:
        return DEFAULT_PUBLISH_TIMEOUT_SECS

    try:
        value = int(env_value)
    except ValueError as err:
        LOGGER.exception("PUBLISH_CHECK_TIMEOUT_SECS must be an integer")
        message = "PUBLISH_CHECK_TIMEOUT_SECS must be an integer"
        raise SystemExit(message) from err
    if value <= 0:
        message = "PUBLISH_CHECK_TIMEOUT_SECS must be a positive integer"
        LOGGER.exception("%s", message)
        raise SystemExit(message)
    return value


@dc.dataclass(frozen=True)
class CommandResult:
    """Result of a cargo command execution."""

    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str


def _drain_stream(
    stream: typ.IO[typ.Any],
    sink: typ.TextIO,
    buffer: list[str],
) -> None:
    """Forward ``stream`` contents into ``sink`` while caching them."""
    read_chunk = getattr(stream, "read1", stream.read)
    decoder: codecs.IncrementalDecoder | None = None

    def emit(text: str) -> None:
        if not text:
            return
        buffer.append(text)
        sink.write(text)
        sink.flush()

    def finalize_decoder(dec: codecs.IncrementalDecoder | None) -> None:
        if dec is None:
            return
        emit(dec.decode(b"", final=True))

    def decode_chunk(
        chunk: bytes | str,
        dec: codecs.IncrementalDecoder | None,
    ) -> tuple[str, codecs.IncrementalDecoder | None]:
        if isinstance(chunk, bytes):
            if dec is None:
                dec = codecs.getincrementaldecoder("utf-8")("replace")
            return dec.decode(chunk), dec
        return str(chunk), dec

    while chunk := read_chunk(4096):
        text, decoder = decode_chunk(chunk, decoder)
        emit(text)
    finalize_decoder(decoder)


def _stream_process_output(
    process: subprocess.Popen[bytes],
    command: Command,
    *,
    timeout_secs: int,
) -> CommandResult:
    """Stream stdout/stderr from ``process`` while capturing their contents."""
    threads, stdout_chunks, stderr_chunks = _start_stream_threads(process)
    try:
        return_code = process.wait(timeout=timeout_secs)
    except subprocess.TimeoutExpired as error:
        _handle_process_timeout(process, threads, command, error)

    _wait_for_stream_threads(threads)
    _close_process_streams(process)
    return CommandResult(
        command=tuple(command),
        return_code=return_code,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )


def _start_stream_threads(
    process: subprocess.Popen[bytes],
) -> tuple[list[threading.Thread], list[str], list[str]]:
    """Start background threads that mirror process output to this console."""
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads: list[threading.Thread] = []
    if process.stdout is not None:
        threads.append(
            threading.Thread(
                target=_drain_stream,
                args=(process.stdout, sys.stdout, stdout_chunks),
                daemon=True,
            )
        )
    if process.stderr is not None:
        threads.append(
            threading.Thread(
                target=_drain_stream,
                args=(process.stderr, sys.stderr, stderr_chunks),
                daemon=True,
            )
        )
    for thread in threads:
        thread.start()
    return threads, stdout_chunks, stderr_chunks


def _wait_for_stream_threads(threads: list[threading.Thread]) -> None:
    """Wait for any running stream mirrors to exit."""
    for thread in threads:
        thread.join()


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    """Close stdout/stderr pipes after streaming completes."""
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            with suppress(Exception):
                stream.close()


def _handle_process_timeout(
    process: subprocess.Popen[bytes],
    threads: list[threading.Thread],
    command: Command,
    error: subprocess.TimeoutExpired,
) -> None:
    """Convert timeout errors into ``ProcessTimedOut`` for callers."""
    process.kill()
    with suppress(Exception):
        process.wait()
    for thread in threads:
        thread.join(timeout=0.1)
    _close_process_streams(process)
    argv = list(getattr(process, "args", command))
    raise ProcessTimedOut(str(error), argv) from error


@dc.dataclass(frozen=True)
class CargoCommandContext:
    """Metadata describing where and how to run a Cargo command."""

    crate: str
    crate_dir: Path
    env_overrides: typ.Mapping[str, str]
    timeout_secs: int


FailureHandler = typ.Callable[[str, CommandResult], bool]


def build_cargo_command_context(
    crate: str,
    workspace_root: Path,
    *,
    timeout_secs: int | None = None,
) -> CargoCommandContext:
    """Create the execution context for a Cargo command.

    The helper resolves the workspace-relative crate directory, initialises the
    environment overrides, and normalises the timeout configuration to simplify
    subsequent :func:`run_cargo_command` invocations.

    Examples
    --------
    >>> context = build_cargo_command_context("tools", Path("/tmp/workspace"))
    >>> context.crate
    'tools'

    """
    crate_dir = workspace_root / "crates" / crate
    env_overrides = {"CARGO_HOME": str(workspace_root / ".cargo-home")}
    resolved_timeout = _resolve_timeout(timeout_secs)
    return CargoCommandContext(
        crate=crate,
        crate_dir=crate_dir,
        env_overrides=env_overrides,
        timeout_secs=resolved_timeout,
    )


def _validate_cargo_command(command: Command) -> None:
    """Ensure the provided command invokes Cargo."""
    if not command or command[0] != "cargo":
        message = "run_cargo_command only accepts cargo invocations"
        raise ValueError(message)


def _execute_cargo_command_with_timeout(
    context: CargoCommandContext,
    command: Command,
) -> CommandResult:
    """Run the Cargo command within the configured workspace context."""
    cargo_invocation = local[command[0]][command[1:]]
    LOGGER.info(
        "Running cargo command for %s: %s",
        context.crate,
        shlex.join(command),
    )
    try:
        with ExitStack() as stack:
            stack.enter_context(local.cwd(context.crate_dir))
            stack.enter_context(local.env(**context.env_overrides))
            process = cargo_invocation.popen(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
            )
            result = _stream_process_output(
                process,
                command,
                timeout_secs=context.timeout_secs,
            )
    except ProcessTimedOut as error:
        LOGGER.exception(
            "cargo command timed out for %s after %s seconds: %s",
            context.crate,
            context.timeout_secs,
            shlex.join(command),
        )
        message = (
            f"cargo command timed out for {context.crate!r} after "
            f"{context.timeout_secs} seconds"
        )
        raise SystemExit(message) from error

    return result


def _handle_cargo_result(
    crate: str,
    result: CommandResult,
    on_failure: FailureHandler | None,
) -> None:
    """Dispatch handling for successful and failed Cargo invocations."""
    if result.return_code == 0:
        _handle_command_output(result.stdout, result.stderr)
        return

    if on_failure is not None and on_failure(crate, result):
        return

    _handle_command_failure(crate, result)


def _handle_command_failure(
    crate: str,
    result: CommandResult,
) -> None:
    """Log diagnostics for a failed Cargo command and abort execution.

    Parameters
    ----------
    crate
        Name of the crate whose Cargo invocation failed.
    result
        The :class:`CommandResult` describing the invocation, including the
        resolved command line and captured output streams.

    """
    joined_command = shlex.join(result.command)
    LOGGER.error("cargo command failed for %s: %s", crate, joined_command)
    if result.stdout:
        LOGGER.error("cargo stdout:%s%s", os.linesep, result.stdout)
    if result.stderr:
        LOGGER.error("cargo stderr:%s%s", os.linesep, result.stderr)
    message = (
        f"cargo command failed for {crate!r}: {joined_command}"
        f" (exit code {result.return_code})"
    )
    raise SystemExit(message)


def _handle_command_output(stdout: str, stderr: str) -> None:
    """Emit captured stdout and stderr from a successful Cargo command."""
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)


def run_cargo_command(
    context: CargoCommandContext,
    command: Command,
    *,
    on_failure: FailureHandler | None = None,
) -> None:
    """Run a Cargo command within the provided execution context.

    Parameters
    ----------
    context
        Execution metadata returned by
        :func:`build_cargo_command_context`.
    command
        Command arguments, which **must** begin with ``cargo``, to execute.
    on_failure
        Optional callback that may handle command failures. When provided the
        handler receives the crate name and :class:`CommandResult`. Returning
        ``True`` suppresses the default error handling, allowing callers to
        decide whether execution should continue.

    Examples
    --------
    Running ``cargo --version`` for a crate directory:

    >>> context = build_cargo_command_context("tools", Path("/tmp/workspace"))
    >>> run_cargo_command(context, ("cargo", "--version"))
    cargo 1.76.0 (9c9d2b9f8 2024-02-16)  # Version output will vary.

    The command honours the ``timeout_secs`` parameter when provided. When it
    is omitted the ``PUBLISH_CHECK_TIMEOUT_SECS`` environment variable is
    consulted before falling back to the default. On failure the captured
    stdout and stderr are logged to aid debugging in CI environments.

    """
    _validate_cargo_command(command)

    result = _execute_cargo_command_with_timeout(context, command)

    _handle_cargo_result(context.crate, result, on_failure)


@dc.dataclass(frozen=True)
class CargoExecutionContext:
    """Context for executing cargo commands in a workspace."""

    crate: str
    workspace_root: Path
    timeout_secs: int | None = None


def _run_cargo_subcommand(
    context: CargoExecutionContext,
    subcommand: str,
    args: typ.Sequence[str],
) -> None:
    command: Command = ("cargo", subcommand, *tuple(args))
    run_cargo_command(
        build_cargo_command_context(
            context.crate,
            context.workspace_root,
            timeout_secs=context.timeout_secs,
        ),
        command,
    )


def _create_cargo_action(
    subcommand: str,
    args: typ.Sequence[str],
    docstring: str,
) -> CrateAction:
    command_args = tuple(args)

    def action(
        crate: str,
        workspace_root: Path,
        *,
        timeout_secs: int | None = None,
    ) -> None:
        context = CargoExecutionContext(
            crate,
            workspace_root,
            timeout_secs,
        )
        _run_cargo_subcommand(
            context,
            subcommand,
            command_args,
        )

    action.__doc__ = docstring
    return typ.cast("CrateAction", action)


package_crate = _create_cargo_action(
    "package",
    ["--allow-dirty", "--no-verify"],
    "Invoke ``cargo package`` for ``crate`` within the exported workspace.",
)


check_crate = _create_cargo_action(
    "check",
    ["--all-features"],
    "Run ``cargo check`` for ``crate`` using the exported workspace.",
)


def _contains_already_published_marker(result: CommandResult) -> bool:
    """Return ``True`` when Cargo output indicates the crate already exists."""
    for stream in (result.stdout, result.stderr):
        if not stream:
            continue

        if isinstance(stream, bytes):
            text = stream.decode("utf-8", errors="ignore")
        else:
            text = str(stream)

        lowered_stream = text.casefold()
        if any(marker in lowered_stream for marker in ALREADY_PUBLISHED_MARKERS_FOLDED):
            return True
    return False


def _publish_one_command(
    crate: str,
    workspace_root: Path,
    command: Command,
    timeout_secs: int | None = None,
) -> bool:
    """Run a publish command, returning ``True`` when publishing should stop.

    When Cargo reports the crate version already exists on crates.io the
    captured output streams are replayed and a warning is emitted. The caller
    can then short-circuit the remaining publish commands for the crate.
    """
    handled = False

    def _on_failure(_crate: str, result: CommandResult) -> bool:
        nonlocal handled

        if not _contains_already_published_marker(result):
            return False

        handled = True
        _handle_command_output(result.stdout, result.stderr)
        LOGGER.warning(
            "crate %s already published on crates.io; skipping remaining commands",
            crate,
        )
        return True

    run_cargo_command(
        build_cargo_command_context(
            crate,
            workspace_root,
            timeout_secs=timeout_secs,
        ),
        command,
        on_failure=_on_failure,
    )
    return handled


def publish_crate_commands(
    crate: str,
    workspace: Path,
    *,
    timeout_secs: int,
) -> None:
    """Run the configured live publish commands for ``crate``.

    Parameters
    ----------
    crate : str
        Name of the crate being published. Must exist in
        :data:`LIVE_PUBLISH_COMMANDS`.
    workspace : Path
        Root directory containing the exported workspace.
    timeout_secs : int
        Timeout in seconds applied to each ``cargo publish`` invocation.

    Raises
    ------
    SystemExit
        Raised when ``crate`` has no live command sequence configured. The
        workflow aborts to avoid silently skipping new crates.

    """
    try:
        commands = LIVE_PUBLISH_COMMANDS[crate]
    except KeyError as error:
        message = f"missing live publish commands for {crate!r}"
        raise SystemExit(message) from error

    for command in commands:
        if _publish_one_command(
            crate,
            workspace,
            command,
            timeout_secs=timeout_secs,
        ):
            break


@dc.dataclass
class CrateProcessingConfig:
    """Configuration for crate processing workflow.

    Parameters
    ----------
    strip_patch : bool
        When ``True`` the ``[patch]`` section is removed before processing.
    include_local_path : bool
        Propagated to :func:`apply_workspace_replacements` to control whether
        crates retain local ``path`` overrides.
    apply_per_crate : bool
        When ``True`` workspace replacements are applied individually for each
        crate rather than once for the entire workspace.
    per_crate_cleanup : Callable[[Path, str], None] | None, optional
        Cleanup action executed after each crate has been processed.

    """

    strip_patch: bool
    include_local_path: bool
    apply_per_crate: bool
    per_crate_cleanup: typ.Callable[[Path, str], None] | None = None


def _process_crates(
    workspace: Path,
    timeout_secs: int,
    config: CrateProcessingConfig,
    crate_action: CrateAction,
) -> None:
    """Coordinate shared crate-processing workflow steps.

    Parameters
    ----------
    workspace : Path
        Path to the exported temporary workspace containing the Cargo manifest
        and crate directories.
    timeout_secs : int
        Timeout applied to each Cargo invocation triggered by the workflow.
    config : CrateProcessingConfig
        Declarative configuration describing how the workspace should be
        prepared and cleaned between crate actions.
    crate_action : CrateAction
        Callable invoked for each crate in :data:`CRATE_ORDER`.

    Examples
    --------
    Run a faux workflow that records the crates it sees::

        >>> tmp = Path("/tmp/workspace")  # doctest: +SKIP
        >>> config = CrateProcessingConfig(  # doctest: +SKIP
        ...     strip_patch=True,
        ...     include_local_path=True,
        ...     apply_per_crate=False
        ... )
        >>> _process_crates(  # doctest: +SKIP
        ...     tmp,
        ...     30,
        ...     config,
        ...     lambda crate, *_: None,
        ... )

    """
    if not CRATE_ORDER:
        message = "CRATE_ORDER must not be empty"
        raise SystemExit(message)

    manifest = workspace / "Cargo.toml"
    if config.strip_patch:
        strip_patch_section(manifest)
    version = workspace_version(manifest)

    if not config.apply_per_crate:
        apply_workspace_replacements(
            workspace,
            version,
            include_local_path=config.include_local_path,
        )

    for crate in CRATE_ORDER:
        if config.apply_per_crate:
            apply_workspace_replacements(
                workspace,
                version,
                include_local_path=config.include_local_path,
                crates=(crate,),
            )

        crate_action(crate, workspace, timeout_secs=timeout_secs)

        if config.per_crate_cleanup is not None:
            config.per_crate_cleanup(manifest, crate)


def _process_crates_for_live_publish(workspace: Path, timeout_secs: int) -> None:
    """Execute the live publish workflow for crates in release order.

    Parameters
    ----------
    workspace : Path
        Path to the exported temporary workspace containing the Cargo
        manifest and crate directories.
    timeout_secs : int
        Timeout applied to each Cargo invocation triggered by the workflow.

    Examples
    --------
    Trigger the live publish workflow after exporting the workspace::

        >>> tmp = Path("/tmp/workspace")  # doctest: +SKIP
        >>> _process_crates_for_live_publish(tmp, 900)  # doctest: +SKIP

    """
    config = CrateProcessingConfig(
        strip_patch=False,
        include_local_path=False,
        apply_per_crate=True,
        per_crate_cleanup=remove_patch_entry,
    )
    _process_crates(workspace, timeout_secs, config, publish_crate_commands)


def _process_crates_for_check(workspace: Path, timeout_secs: int) -> None:
    """Package or check crates locally to validate publish readiness.

    Parameters
    ----------
    workspace : Path
        Path to the exported temporary workspace containing the Cargo
        manifest and crate directories.
    timeout_secs : int
        Timeout applied to each Cargo invocation triggered by the workflow.

    Examples
    --------
    Package and check crates without publishing them::

        >>> tmp = Path("/tmp/workspace")  # doctest: +SKIP
        >>> _process_crates_for_check(tmp, 900)  # doctest: +SKIP

    """

    def _crate_action(crate: str, workspace: Path, *, timeout_secs: int) -> None:
        if crate == "rstest-bdd-patterns":
            package_crate(crate, workspace, timeout_secs=timeout_secs)
        else:
            check_crate(crate, workspace, timeout_secs=timeout_secs)

    config = CrateProcessingConfig(
        strip_patch=True,
        include_local_path=True,
        apply_per_crate=False,
    )
    _process_crates(workspace, timeout_secs, config, _crate_action)


def run_publish_check(*, keep_tmp: bool, timeout_secs: int, live: bool = False) -> None:
    """Run the publish workflow inside a temporary workspace directory.

    The default dry-run mode packages crates locally to validate publish
    readiness. Enable ``live`` to execute ``cargo publish`` for each crate in
    release order once the manifests have been rewritten for crates.io.

    Examples
    --------
    Run the workflow and retain the temporary directory for manual inspection::

        >>> run_publish_check(keep_tmp=True, timeout_secs=120)
        preserving workspace at /tmp/...  # doctest: +ELLIPSIS

    """
    if timeout_secs <= 0:
        message = "timeout-secs must be a positive integer"
        raise SystemExit(message)

    workspace = Path(tempfile.mkdtemp())
    try:
        export_workspace(workspace)
        manifest = workspace / "Cargo.toml"
        prune_workspace_members(manifest)
        if live:
            _process_crates_for_live_publish(workspace, timeout_secs)
        else:
            _process_crates_for_check(workspace, timeout_secs)
    finally:
        if keep_tmp:
            print(f"preserving workspace at {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)


@app.default
def main(
    *,
    timeout_secs: typ.Annotated[
        int,
        Parameter(env_var="PUBLISH_CHECK_TIMEOUT_SECS"),
    ] = DEFAULT_PUBLISH_TIMEOUT_SECS,
    keep_tmp: typ.Annotated[
        bool,
        Parameter(env_var="PUBLISH_CHECK_KEEP_TMP"),
    ] = False,
    live: typ.Annotated[
        bool,
        Parameter(env_var="PUBLISH_CHECK_LIVE"),
    ] = False,
) -> None:
    """Run the publish-check CLI entry point.

    Parameters
    ----------
    timeout_secs : int, optional
        Timeout in seconds for Cargo commands. Defaults to 900 seconds
        (``DEFAULT_PUBLISH_TIMEOUT_SECS``) and may be overridden via the
        ``PUBLISH_CHECK_TIMEOUT_SECS`` environment variable.
    keep_tmp : bool, optional
        When ``True`` the exported workspace directory is retained after the
        workflow finishes. Defaults to ``False`` and may also be set with the
        ``PUBLISH_CHECK_KEEP_TMP`` environment variable.
    live : bool, optional
        When ``True`` runs the live publish workflow instead of a dry run.
        Defaults to ``False`` and may be controlled through the
        ``PUBLISH_CHECK_LIVE`` environment variable.

    Returns
    -------
    None
        This function executes for its side effects and returns ``None``.

    """
    run_publish_check(keep_tmp=keep_tmp, timeout_secs=timeout_secs, live=live)


if __name__ == "__main__":
    app()
