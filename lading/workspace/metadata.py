"""Interfaces for invoking ``cargo metadata``."""

from __future__ import annotations

import json
import logging
import os
import typing as typ

from plumbum import local
from plumbum.commands.processes import CommandNotFound

from lading.utils import normalise_workspace_root
from lading.utils.process import log_command_invocation

if typ.TYPE_CHECKING:
    from pathlib import Path

    from plumbum.commands.base import BoundCommand


class CargoMetadataError(RuntimeError):
    """Raised when ``cargo metadata`` cannot be executed successfully."""

    @classmethod
    def invalid_cmd_mox_timeout(cls) -> CargoMetadataError:
        """Return an error for malformed ``CMOX_IPC_TIMEOUT`` values."""
        return cls("Invalid CMOX_IPC_TIMEOUT value")

    @classmethod
    def non_positive_cmd_mox_timeout(cls) -> CargoMetadataError:
        """Return an error when ``CMOX_IPC_TIMEOUT`` is non-positive."""
        return cls("CMOX_IPC_TIMEOUT must be positive")


class CargoExecutableNotFoundError(CargoMetadataError):
    """Raised when the ``cargo`` executable is missing from ``PATH``."""

    def __init__(self) -> None:
        """Initialise the error with a descriptive message."""
        super().__init__("The 'cargo' executable could not be located.")


class CargoMetadataInvocationError(CargoMetadataError):
    """Raised when ``cargo metadata`` exits with a failure code."""

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        """Summarise the failing invocation for the caller."""
        message = (
            stderr.strip()
            or stdout.strip()
            or f"cargo metadata exited with status {exit_code}"
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


_CMD_MOX_STUB_ENV = "LADING_USE_CMD_MOX_STUB"
CMD_MOX_STUB_ENV_VAR = _CMD_MOX_STUB_ENV
_CMD_MOX_TIMEOUT_DEFAULT = 5.0
_CARGO_PROGRAM = "cargo"
_CARGO_METADATA_ARGS = ("metadata", "--format-version", "1")
_CARGO_METADATA_COMMAND = (_CARGO_PROGRAM, *_CARGO_METADATA_ARGS)


LOGGER = logging.getLogger(__name__)


def _ensure_command() -> BoundCommand | _CmdMoxCommand:
    """Return the ``cargo metadata`` command object."""
    if os.environ.get(_CMD_MOX_STUB_ENV):
        return _build_cmd_mox_command()
    try:
        cargo = local[_CARGO_PROGRAM]
    except CommandNotFound as exc:
        raise CargoExecutableNotFoundError from exc
    return cargo[list(_CARGO_METADATA_ARGS)]


def _coerce_text(value: str | bytes) -> str:
    """Normalise process output to text."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def load_cargo_metadata(
    workspace_root: Path | str | None = None,
) -> typ.Mapping[str, typ.Any]:
    """Execute ``cargo metadata`` and parse the resulting JSON payload."""
    command = _ensure_command()
    root_path = normalise_workspace_root(workspace_root)
    log_command_invocation(LOGGER, _CARGO_METADATA_COMMAND, root_path)
    exit_code, stdout, stderr = command.run(retcode=None, cwd=str(root_path))
    stdout_text = _coerce_text(stdout)
    stderr_text = _coerce_text(stderr)
    if exit_code != 0:
        raise CargoMetadataInvocationError(exit_code, stdout_text, stderr_text)
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise CargoMetadataParseError.invalid_json() from exc
    if not isinstance(payload, dict):
        raise CargoMetadataParseError.non_object_payload()
    return payload


class _CmdMoxCommand:
    """Proxy ``cargo metadata`` through :mod:`cmd_mox`'s IPC server."""

    _ARGS = _CARGO_METADATA_ARGS

    def run(
        self,
        *,
        retcode: int | tuple[int, ...] | None = None,
        cwd: str | os.PathLike[str] | None = None,
    ) -> tuple[int, str, str]:
        """Invoke the cmd-mox IPC server for ``cargo metadata``."""
        ipc, env_mod = _load_cmd_mox_modules()
        socket_path = os.environ.get(env_mod.CMOX_IPC_SOCKET_ENV)
        if not socket_path:
            message = (
                "cmd-mox stub requested for cargo metadata but CMOX_IPC_SOCKET is unset"
            )
            raise CargoMetadataError(message)
        timeout = _resolve_cmd_mox_timeout(os.environ.get(env_mod.CMOX_IPC_TIMEOUT_ENV))
        invocation = ipc.Invocation(
            command=_CARGO_PROGRAM,
            args=list(self._ARGS),
            stdin="",
            env=_build_invocation_environment(cwd),
        )
        response = ipc.invoke_server(invocation, timeout)
        return response.exit_code, response.stdout, response.stderr


def _build_invocation_environment(
    cwd: str | os.PathLike[str] | None,
) -> dict[str, str]:
    """Return environment mapping for the cmd-mox invocation."""
    env = dict(os.environ)
    if cwd is not None:
        env["PWD"] = str(cwd)
    return env


def _load_cmd_mox_modules() -> tuple[typ.Any, typ.Any]:
    """Import cmd-mox modules on demand for the IPC stub."""
    try:
        from cmd_mox import environment as env_mod
        from cmd_mox import ipc
    except ModuleNotFoundError as exc:
        message = (
            "cmd-mox stub requested for cargo metadata but cmd-mox is not available"
        )
        raise CargoMetadataError(message) from exc
    return ipc, env_mod


def _resolve_cmd_mox_timeout(raw_timeout: str | None) -> float:
    """Return the IPC timeout to use when contacting cmd-mox."""
    if raw_timeout is None:
        return _CMD_MOX_TIMEOUT_DEFAULT
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
        raise CargoMetadataError.invalid_cmd_mox_timeout() from exc
    if timeout <= 0:
        raise CargoMetadataError.non_positive_cmd_mox_timeout()
    return timeout


def _build_cmd_mox_command() -> _CmdMoxCommand:
    """Return a command proxy that routes through cmd-mox."""
    return _CmdMoxCommand()
