"""Tests for loading cargo metadata payloads."""

from __future__ import annotations

import collections.abc as cabc
import json
import textwrap
import typing as typ

import pytest

from lading.runtime import CommandSpawnError
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


def test_load_cargo_metadata_suppresses_stdout_echo(tmp_path: Path) -> None:
    """Cargo metadata captures JSON without echoing it to the terminal."""
    recorded_echo_stdout: list[bool] = []

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env
        recorded_echo_stdout.append(echo_stdout)
        return 0, json.dumps(_METADATA_PAYLOAD), ""

    result = load_cargo_metadata(tmp_path, runner=runner)

    assert result == _METADATA_PAYLOAD
    assert recorded_echo_stdout == [False]


def test_load_cargo_metadata_missing_executable(
    tmp_path: Path,
) -> None:
    """Absent ``cargo`` binaries should raise ``CargoExecutableNotFoundError``."""

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env, echo_stdout
        program = "cargo"
        raise CommandSpawnError(program, FileNotFoundError(program))

    with pytest.raises(CargoExecutableNotFoundError):
        load_cargo_metadata(tmp_path, runner=runner)


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


def test_load_cargo_metadata_passes_resolved_cwd(tmp_path: Path) -> None:
    """``load_cargo_metadata`` should invoke the runner in the workspace root."""
    payload = {
        "packages": [],
        "workspace_root": str(tmp_path),
        "workspace_members": [],
    }
    recorded_cwd: list[Path | None] = []

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, env, echo_stdout
        recorded_cwd.append(cwd)
        return 0, json.dumps(payload), ""

    result = load_cargo_metadata(tmp_path, runner=runner)

    assert result == payload
    assert recorded_cwd == [tmp_path.resolve()]
