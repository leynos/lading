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

from __future__ import annotations

import collections.abc as cabc
import contextlib
import contextvars
import json
import typing as typ

from lading.exceptions import LadingError
from lading.runtime import (
    CommandRunner,
    CommandSpawnError,
    coerce_text,
    subprocess_runner,
)
from lading.utils import normalise_workspace_root
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

    @classmethod
    def invalid_json(cls) -> CargoMetadataParseError:
        """Return an error indicating malformed JSON output."""
        return cls("cargo metadata produced invalid JSON output")

    @classmethod
    def non_object_payload(cls) -> CargoMetadataParseError:
        """Return an error indicating the payload was not a JSON object."""
        return cls("cargo metadata returned a non-object JSON payload")


_CARGO_PROGRAM = "cargo"
_CARGO_METADATA_ARGS = ("metadata", "--format-version", "1")
_CARGO_METADATA_COMMAND = (_CARGO_PROGRAM, *_CARGO_METADATA_ARGS)


_COMMAND_RUNNER: contextvars.ContextVar[CommandRunner | None] = contextvars.ContextVar(
    "lading_command_runner",
    default=None,
)


@contextlib.contextmanager
def use_command_runner(runner: CommandRunner) -> cabc.Iterator[None]:
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


def _invoke_cargo_metadata(
    command_runner: CommandRunner,
    root_path: Path | None,
) -> tuple[int, str, str]:
    """Run ``cargo metadata`` and return (exit_code, stdout, stderr) as text."""
    try:
        exit_code, stdout, stderr = command_runner(
            _CARGO_METADATA_COMMAND,
            cwd=root_path,
            echo_stdout=False,
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
        raise CargoMetadataParseError.invalid_json() from exc
    if not isinstance(payload, dict):
        raise CargoMetadataParseError.non_object_payload()
    return payload


def load_cargo_metadata(
    workspace_root: Path | str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> cabc.Mapping[str, typ.Any]:
    """Execute ``cargo metadata`` and parse the resulting JSON payload."""
    root_path = normalise_workspace_root(workspace_root)
    command_runner = _active_command_runner(runner)
    exit_code, stdout_text, stderr_text = _invoke_cargo_metadata(
        command_runner, root_path
    )
    if exit_code != 0:
        raise CargoMetadataInvocationError(exit_code, stdout_text, stderr_text)
    return _parse_cargo_metadata(stdout_text)
