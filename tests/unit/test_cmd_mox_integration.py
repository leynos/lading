"""Tests for cmd-mox command runner integration paths."""

from __future__ import annotations

import logging
import math
import string
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
    raw: str, expected_message: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each class of invalid input raises its own canonical message and logs.

    Pinning the message per input class locks the mapping so the two strings
    cannot be silently swapped between parse failures and out-of-range values.
    A diagnostic warning carrying the rejected raw value is emitted so the
    failure can be traced without reading the source.
    """
    with (
        caplog.at_level(logging.WARNING, logger=cmd_mox_runner.__name__),
        pytest.raises(cmd_mox_runner.CmdMoxError) as excinfo,
    ):
        cmd_mox_runner._resolve_cmd_mox_timeout(raw)
    assert str(excinfo.value) == expected_message
    assert [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "CMOX_IPC_TIMEOUT" in record.getMessage()
        and repr(raw) in record.getMessage()
    ]


def test_ipc_timeout_messages_are_stable(snapshot: SnapshotAssertion) -> None:
    """The canonical IPC-timeout messages change only deliberately."""
    assert snapshot == cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE
    assert snapshot == cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE


class _TimeoutCase(typ.NamedTuple):
    """A timeout input paired with the resolver outcome it should produce."""

    raw: str | None
    resolves_to: float | None
    raises_message: str | None


# Lowercase letters that can never form a float literal: this excludes the
# characters that spell "inf"/"infinity"/"nan" and the exponent marker "e", so
# any string built from them is guaranteed to be unparseable by ``float``.
_NON_NUMERIC_ALPHABET = "".join(
    ch for ch in string.ascii_lowercase if ch not in "einfaty"
)


def _timeout_cases() -> st.SearchStrategy[_TimeoutCase]:
    """Generate timeout inputs grouped by the resolver behaviour they trigger.

    Each input class is constructed directly, so the expected outcome travels
    with the input instead of being re-derived from the implementation's own
    parsing. ``None`` and finite positive floats resolve to a timeout;
    consonant-only strings are unparseable; zero, negative, NaN, and infinite
    values parse but fall outside the finite positive domain.
    """
    default = st.just(_TimeoutCase(None, cmd_mox_runner._CMD_MOX_TIMEOUT_DEFAULT, None))
    finite_positive = st.floats(
        min_value=0.0,
        max_value=1e6,
        exclude_min=True,
        allow_nan=False,
        allow_infinity=False,
    ).map(lambda value: _TimeoutCase(repr(value), value, None))
    unparseable = st.text(alphabet=_NON_NUMERIC_ALPHABET, min_size=1, max_size=12).map(
        lambda raw: _TimeoutCase(raw, None, cmd_mox_runner.INVALID_IPC_TIMEOUT_MESSAGE)
    )
    out_of_range = st.one_of(
        st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
        st.sampled_from([math.nan, math.inf, -math.inf]),
    ).map(
        lambda value: _TimeoutCase(
            repr(value), None, cmd_mox_runner.NON_POSITIVE_IPC_TIMEOUT_MESSAGE
        )
    )
    return st.one_of(default, finite_positive, unparseable, out_of_range)


@given(case=_timeout_cases())
def test_resolve_cmd_mox_timeout_classes(case: _TimeoutCase) -> None:
    """Each input class resolves or raises exactly as its construction declares.

    The expectation is attached to the generated input, so this test asserts
    the contract directly without mirroring the resolver's parsing logic.
    """
    if case.raises_message is None:
        resolved = cmd_mox_runner._resolve_cmd_mox_timeout(case.raw)
        assert resolved == case.resolves_to
        return

    with pytest.raises(cmd_mox_runner.CmdMoxError) as excinfo:
        cmd_mox_runner._resolve_cmd_mox_timeout(case.raw)
    assert str(excinfo.value) == case.raises_message


@given(value=st.one_of(st.none(), st.floats(), st.text(max_size=12)))
def test_resolve_cmd_mox_timeout_is_total(value: float | str | None) -> None:
    """Resolution returns a finite positive timeout or raises ``CmdMoxError``.

    This postcondition holds for every input without restating the parsing
    rules, guarding against a regression that returns an unusable timeout
    (zero, negative, NaN, or infinite) for some unforeseen value.
    """
    raw = value if value is None or isinstance(value, str) else repr(value)
    try:
        resolved = cmd_mox_runner._resolve_cmd_mox_timeout(raw)
    except cmd_mox_runner.CmdMoxError:
        return
    assert math.isfinite(resolved)
    assert resolved > 0


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
