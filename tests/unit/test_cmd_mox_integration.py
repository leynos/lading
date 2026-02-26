"""Tests for cmd-mox integration paths in workspace metadata loading."""

from __future__ import annotations

import typing as typ
from types import SimpleNamespace

import pytest

from lading.workspace import metadata as metadata_module

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_cmd_mox_command_requires_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """cmd-mox stub should fail fast when the socket is not configured."""

    class _StubEnv:
        CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
        CMOX_IPC_TIMEOUT_ENV = "CMOX_IPC_TIMEOUT"

    class _StubIPC:
        class Invocation:
            def __init__(
                self, command: str, args: list[str], stdin: str, env: dict[str, str]
            ) -> None:
                self.command = command
                self.args = args
                self.stdin = stdin
                self.env = env

        def invoke_server(self, invocation: object, timeout: float) -> object:
            message = "invoke_server should not be called without socket"
            raise AssertionError(message)

    monkeypatch.setattr(
        metadata_module, "_load_cmd_mox_modules", lambda: (_StubIPC(), _StubEnv)
    )
    monkeypatch.delenv("CMOX_IPC_SOCKET", raising=False)

    with pytest.raises(
        metadata_module.CargoMetadataError, match="CMOX_IPC_SOCKET is unset"
    ):
        metadata_module._CmdMoxCommand().run()


def test_resolve_cmd_mox_timeout_validates_values() -> None:
    """Timeout parsing should reject non-positive and non-numeric values."""
    assert metadata_module._resolve_cmd_mox_timeout(None) > 0
    assert metadata_module._resolve_cmd_mox_timeout("2.5") == 2.5
    for value in ("0", "-1", "abc"):
        with pytest.raises(metadata_module.CargoMetadataError):
            metadata_module._resolve_cmd_mox_timeout(value)


def test_build_cmd_mox_command_returns_proxy() -> None:
    """The cmd-mox command factory should return a command proxy instance."""
    result = metadata_module._build_cmd_mox_command()

    assert isinstance(result, metadata_module._CmdMoxCommand)


def test_ensure_command_prefers_cmd_mox_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub command should be returned when the environment requests it."""
    sentinel = object()
    monkeypatch.setenv(metadata_module.CMD_MOX_STUB_ENV_VAR, "1")
    monkeypatch.setattr(metadata_module, "_build_cmd_mox_command", lambda: sentinel)

    assert metadata_module._ensure_command() is sentinel


def test_cmd_mox_command_executes_via_ipc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Successful IPC execution should decode and return server responses."""
    socket_path = tmp_path / "cmox" / "socket"
    monkeypatch.setenv("CMOX_IPC_SOCKET", str(socket_path))
    monkeypatch.setenv("CMOX_IPC_TIMEOUT", "1.5")

    class _StubEnv:
        CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
        CMOX_IPC_TIMEOUT_ENV = "CMOX_IPC_TIMEOUT"

    class _StubIPC:
        class Invocation:
            def __init__(
                self, command: str, args: list[str], stdin: str, env: dict[str, str]
            ) -> None:
                self.command = command
                self.args = args
                self.stdin = stdin
                self.env = env

        def __init__(self) -> None:
            self.last_invocation: _StubIPC.Invocation | None = None
            self.timeout: float | None = None

        def invoke_server(self, invocation: object, timeout: float) -> object:
            self.last_invocation = invocation  # type: ignore[assignment]
            self.timeout = timeout
            return SimpleNamespace(exit_code=0, stdout="{}", stderr="")

    ipc = _StubIPC()
    monkeypatch.setattr(
        metadata_module, "_load_cmd_mox_modules", lambda: (ipc, _StubEnv)
    )

    command = metadata_module._CmdMoxCommand()
    exit_code, stdout, stderr = command.run(cwd=str(tmp_path / "workspace"))

    assert command.argv == ("cargo", "metadata", "--format-version", "1")
    assert exit_code == 0
    assert stdout == "{}"
    assert stderr == ""
    assert ipc.last_invocation is not None
    assert ipc.timeout == 1.5
