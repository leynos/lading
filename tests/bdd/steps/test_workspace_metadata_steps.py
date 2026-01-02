"""BDD steps for the cargo metadata wrapper."""

from __future__ import annotations

import json
import textwrap
import typing as typ
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

from lading.workspace import load_cargo_metadata, load_workspace
from tests.helpers.workspace_helpers import install_cargo_stub

if typ.TYPE_CHECKING:
    import pytest
    from cmd_mox import CmdMox

    from lading.workspace import WorkspaceGraph

_FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"

scenarios(str(_FEATURES_DIR / "workspace_metadata.feature"))


@given("a workspace directory", target_fixture="workspace_directory")
def given_workspace_directory(tmp_path: Path) -> Path:
    """Provide a workspace root for discovery exercises."""
    return tmp_path


@given("cargo metadata returns workspace information")
def given_cargo_metadata_response(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    workspace_directory: Path,
) -> None:
    """Stub the ``cargo metadata`` command for discovery tests."""
    install_cargo_stub(cmd_mox, monkeypatch)
    payload = {"workspace_root": str(workspace_directory), "packages": []}
    cmd_mox.mock("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=0,
        stdout=json.dumps(payload),
    )


@when("I inspect the workspace metadata", target_fixture="metadata_payload")
def when_inspect_metadata(workspace_directory: Path) -> typ.Mapping[str, typ.Any]:
    """Execute the discovery helper against the stubbed command."""
    return load_cargo_metadata(workspace_directory)


@then("the metadata payload contains the workspace root")
def then_metadata_contains_workspace(
    metadata_payload: typ.Mapping[str, typ.Any], workspace_directory: Path
) -> None:
    """Assert that the workspace root was parsed from the JSON payload."""
    assert metadata_payload["workspace_root"] == str(workspace_directory)


@given(
    "a workspace crate manifest with a workspace readme",
    target_fixture="crate_manifest",
)
def given_workspace_manifest(workspace_directory: Path) -> Path:
    """Write a workspace member manifest using the workspace README."""
    crate_dir = workspace_directory / "alpha"
    crate_dir.mkdir()
    manifest = crate_dir / "Cargo.toml"
    manifest.write_text(
        textwrap.dedent(
            """
            [package]
            name = "alpha"
            version = "0.1.0"
            readme.workspace = true
            publish = ["crates-io"]
            """
        ).strip()
    )
    return manifest


@given(
    "cargo metadata returns that workspace crate",
    target_fixture="workspace_metadata_payload",
)
def given_workspace_metadata_payload(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    crate_manifest: Path,
    workspace_directory: Path,
) -> dict[str, typ.Any]:
    """Stub metadata for the workspace model scenario."""
    install_cargo_stub(cmd_mox, monkeypatch)
    payload = {
        "workspace_root": str(workspace_directory),
        "packages": [
            {
                "name": "alpha",
                "version": "0.1.0",
                "id": "alpha-id",
                "manifest_path": str(crate_manifest),
                "dependencies": [],
                "publish": ["crates-io"],
            }
        ],
        "workspace_members": ["alpha-id"],
    }
    cmd_mox.mock("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=0,
        stdout=json.dumps(payload),
    )
    return payload


@when("I build the workspace model", target_fixture="workspace_model")
def when_build_workspace_model(
    workspace_directory: Path,
) -> WorkspaceGraph:
    """Construct the workspace graph via the discovery helpers."""
    return load_workspace(workspace_directory)


@then("the workspace model reflects the crate metadata")
def then_workspace_model_reflects_metadata(
    workspace_model: WorkspaceGraph,
    crate_manifest: Path,
    workspace_metadata_payload: dict[str, typ.Any],
) -> None:
    """Verify the workspace graph contains the stubbed crate."""
    assert workspace_model.workspace_root == crate_manifest.parent.parent.resolve()
    expected_name = workspace_metadata_payload["packages"][0]["name"]
    crate = workspace_model.crates[0]
    assert crate.name == expected_name
    assert crate.readme_is_workspace is True
    assert crate.manifest_path == crate_manifest.resolve()
    assert crate.publish is True
