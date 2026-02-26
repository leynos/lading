"""Tests for loading cargo metadata payloads."""

from __future__ import annotations

import json
import logging
import textwrap
import typing as typ

import pytest

from lading.workspace import (
    CargoExecutableNotFoundError,
    CargoMetadataError,
    WorkspaceGraph,
    load_cargo_metadata,
    load_workspace,
)
from lading.workspace import metadata as metadata_module
from tests.helpers.workspace_helpers import install_cargo_stub
from tests.helpers.workspace_metadata import ErrorScenario

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Absent ``cargo`` binaries should raise ``CargoExecutableNotFoundError``."""

    def _raise() -> None:
        raise CargoExecutableNotFoundError

    monkeypatch.setattr(metadata_module, "_ensure_command", _raise)

    with pytest.raises(CargoExecutableNotFoundError):
        load_cargo_metadata(tmp_path)


def test_load_cargo_metadata_error_decodes_byte_streams(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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


def test_load_cmd_mox_modules_errors_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing cmd-mox dependencies should raise CargoMetadataError."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        """Force cmd_mox imports to fail without affecting other modules."""
        if name.startswith("cmd_mox"):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(metadata_module.CargoMetadataError):
        metadata_module._load_cmd_mox_modules()
