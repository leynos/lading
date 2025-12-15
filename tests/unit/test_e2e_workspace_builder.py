"""Unit tests for E2E workspace fixture builders."""

from __future__ import annotations

import json
import typing as typ

from tests.e2e.helpers import workspace_builder

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path


def test_create_nontrivial_workspace_writes_expected_structure(tmp_path: Path) -> None:
    """Build the E2E workspace and verify core files exist."""
    workspace_root = tmp_path / "workspace"
    workspace = workspace_builder.create_nontrivial_workspace(workspace_root)

    assert workspace.root == workspace_root
    assert workspace.crate_names == ("core", "utils", "app")
    assert (workspace_root / "Cargo.toml").exists()
    assert (workspace_root / "README.md").exists()
    assert (workspace_root / "lading.toml").exists()

    readme_text = (workspace_root / "README.md").read_text(encoding="utf-8")
    assert readme_text.count("```") >= 2, (
        "expected README to contain a fenced TOML block"
    )
    assert "```toml" in readme_text
    assert "[dependencies]" in readme_text
    for crate_name in workspace.crate_names:
        assert f'{crate_name} = "0.1.0"' in readme_text

    config_text = (workspace_root / "lading.toml").read_text(encoding="utf-8")
    assert "[bump.documentation]" in config_text
    assert 'globs = ["README.md"]' in config_text
    assert "[publish]" in config_text
    assert 'strip_patches = "all"' in config_text

    for crate_name in workspace.crate_names:
        crate_root = workspace_root / "crates" / crate_name
        assert (crate_root / "Cargo.toml").exists()
        assert (crate_root / "src" / "lib.rs").exists()


def test_create_nontrivial_workspace_metadata_payload_is_json_serialisable(
    tmp_path: Path,
) -> None:
    """Ensure the workspace metadata stub is a JSON-serialisable mapping."""
    workspace_root = tmp_path / "workspace"
    workspace = workspace_builder.create_nontrivial_workspace(workspace_root)

    payload = dict(workspace.cargo_metadata_payload)
    assert payload["workspace_root"] == str(workspace_root)
    assert len(payload["packages"]) == 3
    assert payload["workspace_members"] == ["core-id", "utils-id", "app-id"]
    packages = {package["name"]: package for package in payload["packages"]}

    def _dependency_signature(
        entry: dict[str, object],
    ) -> tuple[object, object, object]:
        return entry.get("name"), entry.get("package"), entry.get("kind")

    utils_deps = packages["utils"]["dependencies"]
    assert {_dependency_signature(dep) for dep in utils_deps} == {
        ("core", "core-id", None),
        ("core", "core-id", "dev"),
    }
    app_deps = packages["app"]["dependencies"]
    assert {_dependency_signature(dep) for dep in app_deps} == {
        ("core", "core-id", None),
        ("utils", "utils-id", None),
        ("core", "core-id", "build"),
    }
    assert json.dumps(payload), "cargo_metadata_payload must be JSON-serialisable"
