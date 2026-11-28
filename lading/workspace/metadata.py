"""Execute and parse ``cargo metadata`` for workspace discovery.

This module is the workspace layer's gateway to ``cargo metadata``: callers use
:func:`load_cargo_metadata` to obtain the parsed JSON payload that downstream
:mod:`lading.workspace` code (such as :mod:`lading.workspace.models`) turns into
the workspace and crate model. It owns the ``CargoMetadataError`` hierarchy,
which classifies the ways the invocation can fail — a missing ``cargo``
executable, a non-zero exit, or unparseable output — so command modules can map
those failures onto their own domain errors.

Execution is delegated to the shared ``CommandRunner`` protocol from
:mod:`lading.runtime` rather than calling :mod:`subprocess` directly. By default
the production :mod:`lading.runtime.subprocess_runner` adapter is used, but
:func:`use_command_runner` installs a context-local override so tests can route
the same calls through the cmd-mox adapter in
:mod:`lading.testing.cmd_mox_runner` without touching the call sites.
"""

import collections.abc as cabc
import contextlib
import contextvars
import dataclasses as dc
import json
import typing as typ

from lading.exceptions import LadingError
from lading.runtime import (
    CommandRunner,
    CommandSpawnError,
    coerce_text,
    subprocess_runner,
)
from lading.utils import normalize_workspace_root
from lading.utils.process import command_detail

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from pathlib import Path


class CargoMetadataError(LadingError):
    """Raised when ``cargo metadata`` cannot be executed successfully."""


class CargoExecutableNotFoundError(CargoMetadataError):
    """Raised when the ``cargo`` executable is missing from ``PATH``."""

    def __init__(self) -> None:
        """Initialise the error with a descriptive message."""
        super().__init__("The 'cargo' executable could not be located.")


class CargoMetadataInvocationError(CargoMetadataError):
    """Raised when ``cargo metadata`` exits with a failure code."""

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        """Summarise the failing invocation for the caller."""
        message = command_detail(stdout, stderr) or (
            f"cargo metadata exited with status {exit_code}"
        )
        super().__init__(message)


class CargoMetadataParseError(CargoMetadataError):
    """Raised when the command output cannot be parsed."""

    def __init__(self, detail: str) -> None:
        """Store the underlying parse failure description."""
        super().__init__(detail)


_CARGO_PROGRAM = "cargo"
_CARGO_METADATA_ARGS = ("metadata", "--format-version", "1")
# ``--no-deps`` keeps discovery to the workspace itself. Bump needs the member
# set only, and resolving the dependency graph is what makes Cargo want to write
# ``Cargo.lock``; publish keeps the full metadata it reasons about.
_CARGO_METADATA_NO_DEPS_ARGS = (*_CARGO_METADATA_ARGS, "--no-deps")


_COMMAND_RUNNER: contextvars.ContextVar[CommandRunner | None] = contextvars.ContextVar(
    "lading_command_runner",
    default=None,
)


@contextlib.contextmanager
def use_command_runner(runner: CommandRunner) -> cabc.Generator[None]:
    """Temporarily route workspace metadata commands through ``runner``."""
    token = _COMMAND_RUNNER.set(runner)
    try:
        yield
    finally:
        _COMMAND_RUNNER.reset(token)


def _active_command_runner(runner: CommandRunner | None = None) -> CommandRunner:
    """Return the explicitly supplied or ambient command runner."""
    if runner is not None:
        return runner
    active_runner = _COMMAND_RUNNER.get()
    if active_runner is None:
        return subprocess_runner
    return active_runner


@dc.dataclass(frozen=True, slots=True)
class MetadataQuery:
    """What one ``cargo metadata`` run is asked for.

    Attributes
    ----------
    no_deps : bool
        Pass ``--no-deps`` so Cargo reports only the workspace members it can
        read from the manifests. Bump discovery uses this to keep the metadata
        phase read-only and cheap; publish keeps the full dependency graph.
    manifest_path : Path | None
        Pass ``--manifest-path`` so Cargo resolves the workspace containing a
        specific manifest. Lockfile planning uses this to learn the effective
        workspace root of each configured or discovered manifest.
    locked : bool
        Pass ``--locked``, so discovery fails rather than updating a stale
        ``Cargo.lock``. ``lading publish --expect-digests`` sets it.

    Examples
    --------
    >>> MetadataQuery(no_deps=True, locked=True).command()
    ('cargo', 'metadata', '--format-version', '1', '--no-deps', '--locked')
    """

    no_deps: bool = False
    manifest_path: Path | None = None
    locked: bool = False

    def command(self) -> tuple[str, ...]:
        """Return the ``cargo metadata`` argument tuple for this query.

        Returns
        -------
        tuple[str, ...]
            The program and its arguments.
        """
        args = list(
            _CARGO_METADATA_NO_DEPS_ARGS if self.no_deps else _CARGO_METADATA_ARGS
        )
        if self.manifest_path is not None:
            args.extend(("--manifest-path", str(self.manifest_path)))
        if self.locked:
            args.append("--locked")
        return (_CARGO_PROGRAM, *args)


def _invoke_cargo_metadata(
    command_runner: CommandRunner, root_path: Path | None, query: MetadataQuery
) -> tuple[int, str, str]:
    """Run ``cargo metadata`` and return (exit_code, stdout, stderr) as text."""
    try:
        exit_code, stdout, stderr = command_runner(
            query.command(), cwd=root_path, echo_stdout=False
        )
    except CommandSpawnError as exc:
        if exc.program == _CARGO_PROGRAM:
            raise CargoExecutableNotFoundError from exc
        raise CargoMetadataError(str(exc)) from exc
    except LadingError as exc:
        raise CargoMetadataError(str(exc)) from exc
    return exit_code, coerce_text(stdout), coerce_text(stderr)


def _parse_cargo_metadata(stdout_text: str) -> cabc.Mapping[str, typ.Any]:
    """Parse and validate the JSON payload produced by ``cargo metadata``."""
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        message = "cargo metadata produced invalid JSON output"
        raise CargoMetadataParseError(message) from exc
    if not isinstance(payload, dict):
        message = "cargo metadata returned a non-object JSON payload"
        raise CargoMetadataParseError(message)
    return payload


def load_cargo_metadata(
    workspace_root: Path | str | None = None,
    *,
    runner: CommandRunner | None = None,
    query: MetadataQuery | None = None,
) -> cabc.Mapping[str, typ.Any]:
    """Execute ``cargo metadata`` and parse the resulting JSON payload.

    Parameters
    ----------
    workspace_root : Path | str | None
        Directory to run ``cargo metadata`` in; ``None`` uses cargo's default.
    runner : CommandRunner | None
        Explicit command runner to use instead of the ambient/default runner.
    query : MetadataQuery | None
        The flags to pass; ``None`` asks for the full dependency graph.

    Returns
    -------
    cabc.Mapping[str, typ.Any]
        Parsed ``cargo metadata`` document.

    Raises
    ------
    CargoMetadataInvocationError
        If ``cargo metadata`` exits with a non-zero status.
    CargoExecutableNotFoundError
        If the ``cargo`` program cannot be found.
    CargoMetadataError
        If the command otherwise fails to spawn or run.
    CargoMetadataParseError
        If the output is not valid JSON, or the payload is not a JSON object.

    Examples
    --------
    >>> from lading.workspace.metadata import load_cargo_metadata
    >>> metadata = load_cargo_metadata()  # doctest: +SKIP
    >>> metadata["workspace_root"]  # doctest: +SKIP
    '...'
    """  # ruff: ignore[docstring-extraneous-exception] -- propagated from the two _*_cargo_metadata helpers
    root_path = normalize_workspace_root(workspace_root)
    command_runner = _active_command_runner(runner)
    exit_code, stdout_text, stderr_text = _invoke_cargo_metadata(
        command_runner, root_path, MetadataQuery() if query is None else query
    )
    if exit_code != 0:
        raise CargoMetadataInvocationError(exit_code, stdout_text, stderr_text)
    return _parse_cargo_metadata(stdout_text)
