"""Tests for cmd-mox command runner integration paths."""

from __future__ import annotations

import math
import typing as typ
from types import SimpleNamespace

import hypothesis.strategies as st
import pytest
from hypothesis import given

from lading.testing import cmd_mox_runner

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


def test_cmd_mox_runner_requires_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """cmd-mox stub should fail fast when the socket is not configured."""
    monkeypatch.delenv("CMOX_IPC_SOCKET", raising=False)

    with pytest.raises(cmd_mox_runner.CmdMoxError, match="CMOX_IPC_SOCKET is unset"):
        cmd_mox_runner.cmd_mox_runner(("cargo", "metadata"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, cmd_mox_runner._CMD_MOX_TIMEOUT_DEFAULT),
        ("2.5", 2.5),
        ("0.001", 0.001),
        ("1000000", 1_000_000.0),
    ],
)
def test_resolve_cmd_mox_timeout_accepts_valid(
    raw: str | None, expected: float
) -> None:
    """A missing or finite positive value resolves to a usable timeout."""
    assert cmd_mox_runner._resolve_cmd_mox_timeout(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected_message"),
    [
        # Unparseable input → the invalid-value message.
        ("abc", cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE),
        ("", cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE),
        ("1.2.3", cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE),
        # Parses but is not a finite positive number → the non-positive message.
        ("0", cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE),
        ("-1", cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE),
        ("nan", cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE),
        ("inf", cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE),
        ("Infinity", cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE),
        # Overflows to infinity without raising in ``float``.
        ("1e400", cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE),
    ],
)
def test_resolve_cmd_mox_timeout_rejects_with_canonical_message(
    raw: str, expected_message: str
) -> None:
    """Each class of invalid input raises its own canonical message.

    Pinning the message per input class locks the mapping so the two strings
    cannot be silently swapped between parse failures and out-of-range values.
    """
    with pytest.raises(cmd_mox_runner.CmdMoxError) as excinfo:
        cmd_mox_runner._resolve_cmd_mox_timeout(raw)
    assert str(excinfo.value) == expected_message


def test_ipc_timeout_messages_are_stable(snapshot: SnapshotAssertion) -> None:
    """The canonical IPC-timeout messages change only deliberately."""
    assert snapshot == cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE
    assert snapshot == cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE


@given(value=st.one_of(st.none(), st.floats(), st.text(max_size=12)))
def test_resolve_cmd_mox_timeout_domain(value: float | str | None) -> None:
    """Resolution is total: default, the parsed positive value, or CmdMoxError.

    ``None`` yields the default and finite positive floats round-trip. Every
    other input raises :class:`CmdMoxError`: unparseable strings carry
    ``INVALID_IPC_TIMEOUT_MESSAGE``, while values that parse but are zero,
    negative, NaN, or infinite carry ``NON_POSITIVE_IPC_TIMEOUT_MESSAGE``.
    """
    raw = value if value is None or isinstance(value, str) else repr(value)

    if raw is None:
        assert (
            cmd_mox_runner._resolve_cmd_mox_timeout(raw)
            == cmd_mox_runner._CMD_MOX_TIMEOUT_DEFAULT
        )
        return

    try:
        parsed = float(raw)
    except ValueError:
        with pytest.raises(cmd_mox_runner.CmdMoxError) as excinfo:
            cmd_mox_runner._resolve_cmd_mox_timeout(raw)
        assert str(excinfo.value) == cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE
        return

    if math.isfinite(parsed) and parsed > 0:
        assert cmd_mox_runner._resolve_cmd_mox_timeout(raw) == parsed
        return

    with pytest.raises(cmd_mox_runner.CmdMoxError) as excinfo:
        cmd_mox_runner._resolve_cmd_mox_timeout(raw)
    assert str(excinfo.value) == cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE


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
