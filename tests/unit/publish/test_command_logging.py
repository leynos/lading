"""Tests covering logging for publish command execution helpers."""

import logging
import sys
import typing as typ

from lading.commands import publish_execution
from lading.testing import cmd_mox_runner

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from cmd_mox.controller import CmdMox

    from lading.runtime import SubprocessContext
    from lading.runtime.relay_events import StreamName


_PROBE_SCRIPT = "print('unused')"


def test_invoke_logs_command_with_cwd(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, use_real_invoke: None
) -> None:
    """``_invoke`` should log the command line and working directory."""
    caplog.set_level(logging.INFO, logger="lading.runtime.subprocess_runner")
    exit_code, stdout, stderr = publish_execution._invoke(
        ("echo", "hello"), cwd=tmp_path
    )

    assert exit_code == 0, "the echo command must exit successfully"
    assert stdout.strip() == "hello", "stdout must carry the echoed payload"
    assert not stderr, "a successful echo must produce no stderr"
    expected = f"Running external command: echo hello (cwd={tmp_path})"
    assert expected in caplog.messages, (
        "the invocation log must name the command and its working directory"
    )


def test_invoke_logs_command_without_cwd(
    caplog: pytest.LogCaptureFixture, use_real_invoke: None
) -> None:
    """``_invoke`` should omit ``cwd`` details when not provided."""
    caplog.set_level(logging.INFO, logger="lading.runtime.subprocess_runner")

    exit_code, stdout, stderr = publish_execution._invoke(("echo", "hello"))

    assert exit_code == 0, "the echo command must exit successfully"
    assert stdout.strip() == "hello", "stdout must carry the echoed payload"
    assert not stderr, "a successful echo must produce no stderr"
    assert "Running external command: echo hello" in caplog.messages, (
        "the invocation log must name the command when no cwd is supplied"
    )
    assert not any("(cwd=" in message for message in caplog.messages), (
        "the log must omit cwd details when the caller supplies none"
    )


def test_invoke_proxies_command_output(
    capsys: pytest.CaptureFixture[str], use_real_invoke: None
) -> None:
    """``_invoke`` should stream stdout/stderr to the parent process."""
    script = """\
import sys
sys.stdout.write("alpha")
sys.stdout.flush()
sys.stderr.write("beta")
sys.stderr.flush()
"""

    exit_code, stdout, stderr = publish_execution._invoke((
        sys.executable,
        "-c",
        script,
    ))

    assert exit_code == 0, "the streaming probe must exit successfully"
    assert stdout == "alpha", "_invoke must return the child's stdout verbatim"
    assert stderr == "beta", "_invoke must return the child's stderr verbatim"
    captured = capsys.readouterr()
    assert captured.out == "alpha", (
        "the child's stdout must be relayed to the parent's stdout"
    )
    assert captured.err.endswith("beta"), (
        "the child's stderr must be relayed to the parent's stderr"
    )


class _PassthroughProbe:
    """Records what the passthrough path passed to its collaborators.

    The stub runner mimics the child writing to the real streams, and the
    echo double records any fallback rendering so the test can prove the
    fallback stayed dormant.
    """

    def __init__(self) -> None:
        """Record the stub's invocations and any echo-fallback payloads."""
        self.calls: list[tuple[str, tuple[str, ...], str | None]] = []
        self.echo_payloads: list[str] = []

    def invoke(
        self,
        program: str,
        args: tuple[str, ...],
        context: SubprocessContext,
    ) -> tuple[int, str, str]:
        """Stand in for ``invoke_via_subprocess``, relaying the child's output."""
        self.calls.append((program, args, context.stdin_data))
        sys.stdout.write("alpha")
        sys.stdout.flush()
        sys.stderr.write("beta")
        sys.stderr.flush()
        return 0, "alpha", "beta"

    def echo(self, payload: str, sink: typ.TextIO, stream: StreamName) -> None:
        """Stand in for ``_echo_buffered_output`` and record any invocation."""
        del sink, stream
        self.echo_payloads.append(payload)


def _assert_passthrough_streamed(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    probe: _PassthroughProbe,
) -> None:
    """Assert the passthrough relayed output and logged exactly once."""
    captured = capsys.readouterr()
    assert captured.out == "alpha", (
        "the stub's stdout must be streamed to the parent process"
    )
    assert captured.err == "beta", (
        "the stub's stderr must be streamed to the parent process"
    )
    assert len(probe.calls) == 1, "the runner must be invoked exactly once for the stub"
    program, args, stdin_data = probe.calls[0]
    assert (program, args) == (sys.executable, ("-c", _PROBE_SCRIPT)), (
        "the runner must receive the stubbed argv"
    )
    assert stdin_data is None, "the stub must receive no stdin payload"
    assert not probe.echo_payloads, (
        "the echo fallback must not run when the stub streams output itself"
    )


# The passthrough path bypasses ``subprocess_runner``, so it must emit the
# single INFO invocation record itself (regression for #104).
def _assert_single_invocation_record(
    caplog: pytest.LogCaptureFixture, script: str
) -> None:
    """Assert exactly one INFO invocation record naming the command line."""
    invocation_records = [
        record
        for record in caplog.records
        if "Running external command" in record.getMessage()
    ]
    assert len(invocation_records) == 1, (
        "the passthrough path must emit exactly one invocation record"
    )
    assert invocation_records[0].levelno == logging.INFO, (
        "the invocation record must be logged at INFO level"
    )
    message = invocation_records[0].getMessage()
    assert "-c" in message, "the logged command line must include the interpreter flags"
    # ``script`` is shell-quoted in the rendered command line, so match on its
    # inner content rather than the raw string.
    assert "unused" in message, (
        "the logged command line must include the script payload"
    )


def test_cmd_mox_passthrough_streams_output(
    cmd_mox: CmdMox,
    request: pytest.FixtureRequest,
) -> None:
    """cmd-mox passthrough should stream via the subprocess runner."""
    capsys: pytest.CaptureFixture[str] = request.getfixturevalue("capsys")
    caplog: pytest.LogCaptureFixture = request.getfixturevalue("caplog")
    monkeypatch: pytest.MonkeyPatch = request.getfixturevalue("monkeypatch")
    request.getfixturevalue("use_real_invoke")
    caplog.set_level(logging.INFO, logger="lading.testing.cmd_mox_runner")
    monkeypatch.setenv("LADING_USE_CMD_MOX_STUB", "1")
    cmd_mox.spy(sys.executable).with_args("-c", _PROBE_SCRIPT).passthrough()

    probe = _PassthroughProbe()
    monkeypatch.setattr(cmd_mox_runner, "invoke_via_subprocess", probe.invoke)
    monkeypatch.setattr(cmd_mox_runner, "_echo_buffered_output", probe.echo)

    exit_code, stdout, stderr = cmd_mox_runner.cmd_mox_runner((
        sys.executable,
        "-c",
        _PROBE_SCRIPT,
    ))

    assert exit_code == 0, "the passthrough stub must exit successfully"
    assert stdout == "alpha", "the passthrough must return the stub's stdout"
    assert stderr == "beta", "the passthrough must return the stub's stderr"
    _assert_passthrough_streamed(capsys, caplog, probe)
    _assert_single_invocation_record(caplog, _PROBE_SCRIPT)
