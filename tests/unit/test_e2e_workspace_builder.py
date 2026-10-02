"""Unit tests for E2E workspace fixture builders."""

import json
import tomllib
import typing as typ

from tests.e2e.helpers import workspace_builder

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


def test_create_nontrivial_workspace_writes_expected_structure(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """Build the E2E workspace and verify core files exist."""
    workspace_root = tmp_path / "workspace"
    workspace = workspace_builder.create_nontrivial_workspace(workspace_root)

    assert workspace.root == workspace_root, (
        "the built workspace must be rooted at the requested directory"
    )
    assert workspace.crate_names == ("core", "utils", "app"), (
        "the fixture must build exactly the core, utils, and app crates"
    )
    assert (workspace_root / "Cargo.toml").exists(), (
        "the workspace root manifest must be written"
    )
    assert (workspace_root / "README.md").exists(), (
        "the fixture must write the workspace README"
    )
    assert (workspace_root / "lading.toml").exists(), (
        "the fixture must write the lading configuration"
    )

    readme_text = (workspace_root / "README.md").read_text(encoding="utf-8")
    assert readme_text.count("```") >= 2, (
        "expected README to contain a fenced TOML block"
    )
    assert "```toml" in readme_text, "the README must carry a TOML-tagged fenced block"
    assert "[dependencies]" in readme_text, (
        "the README's TOML block must declare a dependencies table"
    )
    for crate_name in workspace.crate_names:
        assert f'{crate_name} = "{workspace.version}"' in readme_text, (
            f"the README must pin {crate_name} to the workspace version"
        )

    config = tomllib.loads((workspace_root / "lading.toml").read_text(encoding="utf-8"))
    assert config == snapshot, "expected the parsed workspace configuration"

    for crate_name in workspace.crate_names:
        crate_root = workspace_root / "crates" / crate_name
        assert (crate_root / "Cargo.toml").exists(), (
            f"the {crate_name} crate must have a manifest under crates/"
        )
        assert (crate_root / "src" / "lib.rs").exists(), (
            f"the {crate_name} crate must have a library entry point"
        )


def test_create_nontrivial_workspace_metadata_payload_is_json_serialisable(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """Ensure the workspace metadata stub is a JSON-serialisable mapping."""
    workspace_root = tmp_path / "workspace"
    workspace = workspace_builder.create_nontrivial_workspace(workspace_root)

    payload = dict(workspace.cargo_metadata_payload)
    assert payload["workspace_root"] == str(workspace_root), (
        "the metadata payload must name the real workspace root"
    )
    assert len(payload["packages"]) == 3, (
        "the metadata payload must describe all three fixture crates"
    )
    assert payload["workspace_members"] == ["core-id", "utils-id", "app-id"], (
        "the metadata payload must list every fixture crate id in order"
    )
    packages = {package["name"]: package for package in payload["packages"]}

    def _dependency_signature(
        entry: dict[str, object],
    ) -> tuple[object | None, object | None, object | None]:
        return entry.get("name"), entry.get("package"), entry.get("kind")

    dependency_signatures = {
        name: {_dependency_signature(dep) for dep in packages[name]["dependencies"]}
        for name in ("utils", "app")
    }
    assert dependency_signatures == snapshot, (
        "expected utils to depend on core normally and for dev, and app to "
        "depend on core, utils, and a build dependency"
    )
    json.dumps(
        payload
    )  # Raises TypeError/ValueError if payload isn't JSON-serialisable.
