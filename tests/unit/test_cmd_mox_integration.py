"""Tests for cmd-mox command runner integration paths."""

from __future__ import annotations

import typing as typ
from types import SimpleNamespace

import pytest

from lading.testing import cmd_mox_runner

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_cmd_mox_runner_requires_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """cmd-mox stub should fail fast when the socket is not configured."""
    monkeypatch.delenv("CMOX_IPC_SOCKET", raising=False)

    with pytest.raises(cmd_mox_runner.CmdMoxError, match="CMOX_IPC_SOCKET is unset"):
        cmd_mox_runner.cmd_mox_runner(("cargo", "metadata"))


def test_resolve_cmd_mox_timeout_validates_values() -> None:
    """Timeout parsing should reject non-positive and non-numeric values."""
    assert cmd_mox_runner._resolve_cmd_mox_timeout(None) > 0
    assert cmd_mox_runner._resolve_cmd_mox_timeout("2.5") == 2.5
    for value in ("0", "-1", "abc"):
        with pytest.raises(cmd_mox_runner.CmdMoxError):
            cmd_mox_runner._resolve_cmd_mox_timeout(value)


def test_cmd_mox_runner_executes_via_ipc(
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
            self.last_invocation = typ.cast("_StubIPC.Invocation", invocation)
            self.timeout = timeout
            return SimpleNamespace(exit_code=0, stdout="{}", stderr="")

    ipc = _StubIPC()
    monkeypatch.setattr(cmd_mox_runner, "env_mod", _StubEnv)
    monkeypatch.setattr(cmd_mox_runner, "ipc", ipc)

    exit_code, stdout, stderr = cmd_mox_runner.cmd_mox_runner(
        ("cargo", "metadata", "--format-version", "1"),
        cwd=tmp_path / "workspace",
    )

    assert exit_code == 0
    assert stdout == "{}"
    assert stderr == ""
    assert ipc.last_invocation is not None
    assert ipc.last_invocation.command == "cargo"
    assert ipc.last_invocation.args == ["metadata", "--format-version", "1"]
    assert ipc.last_invocation.env["PWD"] == str(tmp_path / "workspace")
    assert ipc.timeout == 1.5
