"""Tests for the ``cargo metadata`` wrapper."""

from __future__ import annotations

import dataclasses as dc
import json
import logging
import textwrap
import typing as typ
from types import SimpleNamespace

import pytest

from lading.workspace import (
    CargoExecutableNotFoundError,
    CargoMetadataError,
    WorkspaceDependency,
    WorkspaceGraph,
    WorkspaceModelError,
    build_workspace_graph,
    load_cargo_metadata,
    load_workspace,
)
from lading.workspace import metadata as metadata_module
from tests.helpers.workspace_helpers import install_cargo_stub
from tests.helpers.workspace_metadata import build_test_package, create_test_manifest

_METADATA_PAYLOAD: typ.Final[dict[str, typ.Any]] = {
    "workspace_root": "./",
    "packages": [],
}

if typ.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox import CmdMox


@pytest.mark.parametrize(
    "output_data",
    [
        pytest.param(
            (json.dumps(_METADATA_PAYLOAD), ""),
            id="text",
        ),
        pytest.param(
            (json.dumps(_METADATA_PAYLOAD).encode("utf-8"), b""),
            id="bytes",
        ),
    ],
)
def test_load_cargo_metadata_handles_stdout_variants(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output_data: tuple[str | bytes, str | bytes],
) -> None:
    """Successful invocations should return parsed JSON for text and byte streams."""
    install_cargo_stub(cmd_mox, monkeypatch)
    stdout_data, stderr_data = output_data
    cmd_mox.mock("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=0,
        stdout=stdout_data,
        stderr=stderr_data,
    )

    result = load_cargo_metadata(tmp_path)

    assert result == _METADATA_PAYLOAD


def test_load_cargo_metadata_logs_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure the command invocation is logged with resolved arguments."""

    class _Command:
        argv = (
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
        )

        def run(
            self,
            *,
            retcode: int | tuple[int, ...] | None = None,
            cwd: str | Path | None = None,
        ) -> tuple[int, str, str]:
            return 0, json.dumps(_METADATA_PAYLOAD), ""

    monkeypatch.setattr(metadata_module, "_ensure_command", lambda: _Command())
    monkeypatch.delenv(metadata_module.CMD_MOX_STUB_ENV_VAR, raising=False)
    caplog.set_level(logging.INFO, logger="lading.workspace.metadata")

    result = load_cargo_metadata(tmp_path)

    assert result == _METADATA_PAYLOAD
    assert (
        "Running external command: cargo metadata --format-version 1 --locked"
        in caplog.text
    )


def test_load_cargo_metadata_missing_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absent ``cargo`` binaries should raise ``CargoExecutableNotFoundError``."""

    def _raise() -> None:
        raise CargoExecutableNotFoundError

    monkeypatch.setattr(metadata_module, "_ensure_command", _raise)

    with pytest.raises(CargoExecutableNotFoundError):
        load_cargo_metadata(tmp_path)


def test_load_cargo_metadata_error_decodes_byte_streams(
    cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failure messages should be decoded when provided as bytes."""
    install_cargo_stub(cmd_mox, monkeypatch)
    cmd_mox.mock("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=101,
        stdout=b"",
        stderr=b"manifest missing",
    )

    with pytest.raises(CargoMetadataError) as excinfo:
        load_cargo_metadata(tmp_path)

    assert "manifest missing" in str(excinfo.value)


@dc.dataclass(frozen=True, slots=True)
class ErrorScenario:
    """Test scenario for cargo metadata error cases."""

    exit_code: int
    stdout: str
    stderr: str
    expected_message: str


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            ErrorScenario(
                exit_code=101,
                stdout="",
                stderr="could not read manifest",
                expected_message="could not read manifest",
            ),
            id="non_zero_exit_with_stderr",
        ),
        pytest.param(
            ErrorScenario(
                exit_code=101,
                stdout="",
                stderr="",
                expected_message="cargo metadata exited with status 101",
            ),
            id="non_zero_exit_empty_output",
        ),
        pytest.param(
            ErrorScenario(
                exit_code=0,
                stdout="[]",
                stderr="",
                expected_message="non-object",
            ),
            id="non_object_json",
        ),
        pytest.param(
            ErrorScenario(
                exit_code=0,
                stdout="{]",
                stderr="",
                expected_message="invalid JSON",
            ),
            id="malformed_json",
        ),
    ],
)
def test_load_cargo_metadata_error_scenarios(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: ErrorScenario,
) -> None:
    """Error cases should raise :class:`CargoMetadataError` with detail."""
    install_cargo_stub(cmd_mox, monkeypatch)
    cmd_mox.mock("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=scenario.exit_code,
        stdout=scenario.stdout,
        stderr=scenario.stderr,
    )

    with pytest.raises(CargoMetadataError) as excinfo:
        load_cargo_metadata(tmp_path)

    assert scenario.expected_message in str(excinfo.value)


def test_ensure_command_raises_on_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ``CommandNotFound`` is surfaced as ``CargoExecutableNotFoundError``."""

    class _RaisingLocal:
        def __getitem__(self, name: str) -> typ.NoReturn:
            raise metadata_module.CommandNotFound(name, ["/usr/bin"])

    monkeypatch.setattr(metadata_module, "local", _RaisingLocal())

    with pytest.raises(CargoExecutableNotFoundError):
        metadata_module._ensure_command()


def test_build_workspace_graph_constructs_models(tmp_path: Path) -> None:
    """Convert metadata payloads into strongly typed workspace models."""
    workspace_root = tmp_path
    crate_manifest = create_test_manifest(
        workspace_root,
        "crate",
        """
        [package]
        name = "crate"
        version = "0.1.0"
        readme.workspace = true

        [dependencies]
        helper = { path = "../helper", version = "0.1.0" }
        """,
    )
    helper_manifest = create_test_manifest(
        workspace_root,
        "helper",
        """
        [package]
        name = "helper"
        version = "0.1.0"
        readme = "README.md"
        """,
    )
    metadata = {
        "workspace_root": str(workspace_root),
        "packages": [
            build_test_package(
                "crate",
                "0.1.0",
                crate_manifest,
                dependencies=[
                    {"name": "helper", "package": "helper-id", "kind": "dev"},
                    {"name": "external", "package": "external-id"},
                ],
                publish=[],
            ),
            build_test_package(
                "helper",
                "0.1.0",
                helper_manifest,
                publish=["crates-io"],
            ),
        ],
        "workspace_members": ["crate-id", "helper-id"],
    }

    graph = build_workspace_graph(metadata)

    assert isinstance(graph, WorkspaceGraph)
    assert graph.workspace_root == workspace_root.resolve()
    crates = graph.crates_by_name
    assert set(crates) == {"crate", "helper"}
    crate = crates["crate"]
    assert crate.publish is False
    assert crate.readme_is_workspace is True
    assert crate.dependencies == (
        WorkspaceDependency(
            package_id="helper-id",
            name="helper",
            manifest_name="helper",
            kind="dev",
        ),
    )
    helper = crates["helper"]
    assert helper.publish is True
    assert helper.readme_is_workspace is False
    assert helper.dependencies == ()


def test_build_workspace_graph_rejects_missing_members(tmp_path: Path) -> None:
    """Missing package entries should surface as ``WorkspaceModelError``."""
    metadata = {
        "workspace_root": str(tmp_path),
        "packages": [],
        "workspace_members": ["crate-id"],
    }

    with pytest.raises(
        WorkspaceModelError,
        match=r"workspace member 'crate-id' missing from package list",
    ):
        build_workspace_graph(metadata)


def test_load_workspace_invokes_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ensure ``load_workspace`` converts metadata into a graph."""
    crate_manifest = tmp_path / "crate" / "Cargo.toml"
    crate_manifest.parent.mkdir(parents=True)
    crate_manifest.write_text(
        textwrap.dedent(
            """
            [package]
            name = "crate"
            version = "0.1.0"
            readme.workspace = true
            """
        ).strip()
    )
    metadata = {
        "workspace_root": str(tmp_path),
        "packages": [
            {
                "name": "crate",
                "version": "0.1.0",
                "id": "crate-id",
                "manifest_path": str(crate_manifest),
                "dependencies": [],
                "publish": None,
            }
        ],
        "workspace_members": ["crate-id"],
    }

    def _fake_load_cargo_metadata(
        workspace_root: Path | str | None = None,
    ) -> dict[str, typ.Any]:
        return metadata

    monkeypatch.setattr(
        metadata_module, "load_cargo_metadata", _fake_load_cargo_metadata
    )

    graph = load_workspace(tmp_path)

    assert isinstance(graph, WorkspaceGraph)
    assert graph.crates[0].name == "crate"


def test_load_cargo_metadata_logs_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``load_cargo_metadata`` should log the command it executes."""
    payload = {
        "packages": [],
        "workspace_root": str(tmp_path),
        "workspace_members": [],
    }

    class _FakeCommand:
        def run(
            self,
            *,
            retcode: int | tuple[int, ...] | None = None,
            cwd: object | None = None,
        ) -> tuple[int, str, str]:
            return 0, json.dumps(payload), ""

    monkeypatch.setattr(metadata_module, "_ensure_command", lambda: _FakeCommand())

    caplog.set_level(logging.INFO, logger="lading.workspace.metadata")
    result = load_cargo_metadata(tmp_path)

    assert result == payload
    expected = (
        "Running external command: cargo metadata --format-version 1 "
        f"(cwd={tmp_path.resolve()})"
    )
    assert expected in caplog.messages


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
    """Timeout parsing should reject non-positive values."""
    assert metadata_module._resolve_cmd_mox_timeout(None) > 0
    assert metadata_module._resolve_cmd_mox_timeout("2.5") == 2.5
    with pytest.raises(metadata_module.CargoMetadataError):
        metadata_module._resolve_cmd_mox_timeout("0")


def test_build_invocation_environment_sets_pwd(tmp_path: Path) -> None:
    """The invocation environment should include PWD when cwd is provided."""
    working_dir = tmp_path / "work"
    env = metadata_module._build_invocation_environment(str(working_dir))

    assert env["PWD"] == str(working_dir)


def test_coerce_text_handles_bytes() -> None:
    """Byte streams should be decoded to strings."""
    assert metadata_module._coerce_text(b"bytes") == "bytes"


def test_error_convenience_constructors() -> None:
    """Helper constructors should expose descriptive messages."""
    assert "Invalid CMOX_IPC_TIMEOUT value" in str(
        metadata_module.CargoMetadataError.invalid_cmd_mox_timeout()
    )
    assert "must be positive" in str(
        metadata_module.CargoMetadataError.non_positive_cmd_mox_timeout()
    )


def test_load_cmd_mox_modules_errors_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing cmd-mox dependencies should raise CargoMetadataError."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("cmd_mox"):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(metadata_module.CargoMetadataError):
        metadata_module._load_cmd_mox_modules()


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
