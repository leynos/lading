"""Focused tests for publish execution helpers."""

import importlib
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lading.commands import publish_execution
from lading.commands.publish import PublishPreflightError
from lading.testing import cmd_mox_runner

subprocess_runner = importlib.import_module("lading.runtime.subprocess_runner")


class _MockCmdMoxEnv:
    """Minimal cmd-mox environment module stub."""

    CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
    CMOX_IPC_TIMEOUT_ENV = "CMOX_IPC_TIMEOUT"
    CMOX_REAL_COMMAND_ENV_PREFIX = "CMOX_REAL_"


class _MockCmdMoxIPC:
    """Minimal cmd-mox IPC module stub for passthrough handling."""

    class Response:
        """Sentinel response type used by passthrough resolution checks."""

    class PassthroughResult:
        """Payload sent back to cmd-mox after passthrough execution."""

        def __init__(
            self,
            invocation_id: str,
            stdout: str,
            stderr: str,
            exit_code: int,
        ) -> None:
            self.invocation_id = invocation_id
            self.stdout = stdout
            self.stderr = stderr
            self.exit_code = exit_code

    def report_passthrough_result(self, result: object, timeout: float) -> object:
        """Return ``result`` to emulate reporting a passthrough result."""
        del timeout
        return result


class _MockCommandRunner:
    """Minimal cmd-mox command runner stub for passthrough resolution."""

    def prepare_environment(
        self,
        lookup_path: str,
        extra_env: dict[str, str],
        invocation_env: dict[str, str],
    ) -> dict[str, str]:
        """Merge lookup path, extra env, and invocation env."""
        return {"PATH": lookup_path} | extra_env | invocation_env

    def resolve_command_with_override(
        self, command: str, path: str, override: str | None
    ) -> Path:
        """Resolve the underlying command to the current Python executable."""
        del command, path, override
        return Path(sys.executable)


@pytest.fixture
def mock_cmd_mox_modules(tmp_path: Path) -> SimpleNamespace:
    """Provide complete cmd-mox module stubs for passthrough handling."""

    class _Env:
        CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
        CMOX_REAL_COMMAND_ENV_PREFIX = "CMOX_REAL_"

    class _IPC:
        class Response:
            def __init__(
                self, stdout: str = "", stderr: str = "", exit_code: int = 0
            ) -> None:
                self.stdout = stdout
                self.stderr = stderr
                self.exit_code = exit_code

        class PassthroughResult:
            def __init__(
                self, invocation_id: str, stdout: str, stderr: str, exit_code: int
            ) -> None:
                self.invocation_id = invocation_id
                self.stdout = stdout
                self.stderr = stderr
                self.exit_code = exit_code

        def report_passthrough_result(self, result: object, timeout: float) -> Response:
            return self.Response(
                stdout=getattr(result, "stdout", ""),
                stderr=getattr(result, "stderr", ""),
                exit_code=getattr(result, "exit_code", 0),
            )

    class _CommandRunner:
        def prepare_environment(
            self,
            lookup_path: str,
            extra_env: dict[str, str],
            invocation_env: dict[str, str],
        ) -> dict[str, str]:
            shim_dir = tmp_path / "cmox" / "shim"
            return (
                {"PATH": f"{lookup_path}{os.pathsep}{shim_dir}{os.pathsep}/usr/bin"}
                | extra_env
                | invocation_env
            )

        def resolve_command_with_override(
            self, command: str, path: str, override: str | None
        ) -> _IPC.Response:
            return _IPC.Response(stdout="pass", stderr="through", exit_code=0)

    return SimpleNamespace(
        env_module=_Env,
        ipc_module=_IPC,
        command_runner=_CommandRunner,
    )


def test_build_cmd_mox_invocation_env_merges_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit env values should override the base invocation environment."""
    monkeypatch.setenv("EXISTING", "keep")
    workspace_root = tmp_path / "workspace"
    value_path = workspace_root / "value"
    env = cmd_mox_runner._build_cmd_mox_invocation_env(
        workspace_root,
        {"NEW": value_path},
    )

    assert env["PWD"] == str(workspace_root)
    assert env["NEW"] == str(value_path)
    assert env["EXISTING"] == "keep"


def test_build_cmd_mox_invocation_env_prefers_cwd_over_pwd_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit cwd should win even when env overrides include PWD."""
    workspace_root = tmp_path / "workspace"
    env = cmd_mox_runner._build_cmd_mox_invocation_env(
        workspace_root,
        {"PWD": "/root/repo", "OTHER": "ok"},
    )

    assert env["PWD"] == str(workspace_root)
    assert env["OTHER"] == "ok"


def test_process_cmd_mox_response_updates_environment(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Buffered cmd-mox responses should update os.environ and be echoed."""
    monkeypatch.delenv("ADDED_VAR", raising=False)

    class _Response:
        def __init__(self) -> None:
            self.env = {"ADDED_VAR": "yes"}
            self.stdout = "out\n"
            self.stderr = "err\n"
            self.exit_code = 3

    exit_code, stdout, stderr = cmd_mox_runner._process_cmd_mox_response(
        _Response(),
        streamed=False,
    )
    captured = capsys.readouterr()

    assert os.environ["ADDED_VAR"] == "yes"
    assert captured.out.endswith("out\n")
    assert captured.err.endswith("err\n")
    assert exit_code == 3
    assert stdout == "out\n"
    assert stderr == "err\n"


def test_process_cmd_mox_response_rejects_missing_exit_code() -> None:
    """Malformed cmd-mox responses should not be treated as success."""
    response = SimpleNamespace(stdout="out", stderr="err")

    with pytest.raises(ValueError, match="exit code"):
        cmd_mox_runner._process_cmd_mox_response(response, streamed=False)


def test_handle_cmd_mox_passthrough_returns_unmodified_response() -> None:
    """When no passthrough directive is present, the response should be returned."""
    response = SimpleNamespace()
    invocation = SimpleNamespace(env={}, command="", args=(), stdin="")

    returned, streamed = cmd_mox_runner._handle_cmd_mox_passthrough(
        response, invocation, timeout=1.0
    )

    assert returned is response
    assert streamed is False


def test_handle_cmd_mox_passthrough_reports_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mock_cmd_mox_modules: SimpleNamespace,
) -> None:
    """Passthrough directives resolved to responses should be reported back."""
    socket_path = str(tmp_path / "cmox" / "shim" / "socket")
    monkeypatch.setenv("CMOX_IPC_SOCKET", socket_path)
    monkeypatch.setattr(cmd_mox_runner, "env_mod", mock_cmd_mox_modules.env_module)
    monkeypatch.setattr(cmd_mox_runner, "ipc", mock_cmd_mox_modules.ipc_module())
    monkeypatch.setattr(
        cmd_mox_runner, "command_runner", mock_cmd_mox_modules.command_runner()
    )

    directive = SimpleNamespace(
        invocation_id="123",
        lookup_path=str(tmp_path / "cmox" / "bin"),
        extra_env={"EXTRA": "1"},
    )
    invocation = SimpleNamespace(
        env={"PATH": str(tmp_path / "cmox" / "bin")},
        command="cargo",
        args=("test",),
        stdin="",
    )
    response = SimpleNamespace(passthrough=directive)

    returned, streamed = cmd_mox_runner._handle_cmd_mox_passthrough(
        response,
        invocation,
        timeout=1.0,
    )

    assert streamed is False
    assert isinstance(returned, mock_cmd_mox_modules.ipc_module.Response)
    assert returned.stdout == "pass"


def test_handle_cmd_mox_passthrough_uses_pwd_for_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Passthrough subprocesses should run with cwd derived from PWD."""
    shim_socket = tmp_path / "cmox" / "shim" / "socket"
    shim_socket.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CMOX_IPC_SOCKET", str(shim_socket))
    monkeypatch.setattr(cmd_mox_runner, "env_mod", _MockCmdMoxEnv())
    monkeypatch.setattr(cmd_mox_runner, "ipc", _MockCmdMoxIPC())
    monkeypatch.setattr(cmd_mox_runner, "command_runner", _MockCommandRunner())

    directive = SimpleNamespace(
        invocation_id="cwd-test",
        lookup_path=str(tmp_path / "cmox" / "bin"),
        extra_env={},
    )
    expected_cwd = tmp_path / "workspace"
    invocation = SimpleNamespace(
        env={"PATH": str(tmp_path / "cmox" / "bin"), "PWD": str(expected_cwd)},
        command="git",
        args=("status",),
        stdin="",
    )
    captured: dict[str, Path | None] = {"cwd": None}

    def _fake_invoke_via_subprocess(
        program: str,
        args: tuple[str, ...],
        context: subprocess_runner.SubprocessContext,
    ) -> tuple[int, str, str]:
        del program, args
        captured["cwd"] = context.cwd
        return 0, "", ""

    monkeypatch.setattr(
        cmd_mox_runner, "invoke_via_subprocess", _fake_invoke_via_subprocess
    )
    response = SimpleNamespace(passthrough=directive)

    returned, streamed = cmd_mox_runner._handle_cmd_mox_passthrough(
        response,
        invocation,
        timeout=1.0,
    )

    assert streamed is True
    assert isinstance(returned, _MockCmdMoxIPC.PassthroughResult)
    assert captured["cwd"] == expected_cwd


def test_invoke_via_subprocess_surfaces_spawn_errors() -> None:
    """Failed spawns should raise PublishPreflightError with context."""
    with pytest.raises(PublishPreflightError):
        publish_execution._invoke(("definitely-not-a-command",))


def test_invoke_via_subprocess_writes_stdin() -> None:
    """Successful invocations should write provided stdin data."""
    context = subprocess_runner.SubprocessContext(
        cwd=None, env=None, stdin_data="payload"
    )
    script = "import sys; data=sys.stdin.read(); sys.stdout.write(data)"

    exit_code, stdout, stderr = subprocess_runner.invoke_via_subprocess(
        sys.executable,
        ("-c", script),
        context,
    )

    assert exit_code == 0
    assert stdout == "payload"
    assert stderr == ""


def test_normalise_environment_stringifies_values() -> None:
    """Environment dictionaries should be coerced to string values."""
    result = subprocess_runner.normalise_environment({"PATH": Path.cwd()})

    assert result == {"PATH": str(Path.cwd())}


def test_merge_cmd_mox_path_entries_filters_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shim directories should be removed and duplicate entries deduplicated."""
    shim_dir = tmp_path / "cmox" / "shim"
    monkeypatch.setenv("CMOX_IPC_SOCKET", f"{shim_dir}/socket")

    merged = cmd_mox_runner._merge_cmd_mox_path_entries(
        f"{shim_dir}{os.pathsep}/usr/bin{os.pathsep}",
        f"/opt/tools{os.pathsep}/usr/bin",
    )

    assert shim_dir.as_posix() not in merged
    assert "/usr/bin" in merged
    assert "/opt/tools" in merged


def test_relay_stream_decodes_and_buffers_text() -> None:
    """Stream relaying should buffer decoded text and write to sinks."""
    source = io.BytesIO("hello\nworld\u20ac".encode()[:-1])
    sink = io.StringIO()
    buffer: list[str] = []

    subprocess_runner.relay_stream(source, sink, buffer)

    assert "".join(buffer).startswith("hello\nworld")
    assert sink.getvalue().startswith("hello\nworld")


def test_write_to_sink_handles_broken_pipe() -> None:
    """Broken pipes should be swallowed and return None."""

    class _Broken:
        def write(self, _: str) -> None:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    assert subprocess_runner.write_to_sink(None, "payload") is None
    assert subprocess_runner.write_to_sink(_Broken(), "payload") is None


def test_apply_cmd_mox_environment_and_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment updates and buffered echoing should be no-ops for empty input."""
    monkeypatch.delenv("NEW_CMD_ENV", raising=False)

    cmd_mox_runner._apply_cmd_mox_environment({"NEW_CMD_ENV": "present"})
    cmd_mox_runner._echo_buffered_output("", io.StringIO())

    assert os.environ["NEW_CMD_ENV"] == "present"


def test_cmd_mox_shim_directory_without_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shim directory helper should return None when socket is unset."""
    monkeypatch.delenv("CMOX_IPC_SOCKET", raising=False)

    assert cmd_mox_runner._cmd_mox_shim_directory() is None


def test_log_subprocess_environment_redacts_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Environment logging should redact common secret tokens."""
    caplog.set_level("DEBUG", logger="lading.runtime.subprocess_runner")

    subprocess_runner._log_subprocess_environment({
        "TOKEN": "secret",
        "PATH": "/usr/bin",
    })

    assert "PATH" in caplog.text
    assert "<redacted>" in caplog.text
