"""Unit tests for E2E workspace fixture builders."""

from __future__ import annotations

import json
import typing as typ

from tests.e2e.helpers import workspace_builder

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path
else:  # pragma: no cover - runtime typing fallback
    Path = typ.Any  # type: ignore[assignment]


def test_create_nontrivial_workspace_writes_expected_structure(tmp_path: Path) -> None:
    """Build the E2E workspace and verify core files exist."""
    workspace_root = tmp_path / "workspace"
    workspace = workspace_builder.create_nontrivial_workspace(workspace_root)

    assert workspace.root == workspace_root
    assert workspace.crate_names == ("core", "utils", "app")
    assert (workspace_root / "Cargo.toml").exists()
    assert (workspace_root / "README.md").exists()
    assert (workspace_root / "lading.toml").exists()

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
    json.dumps(payload)
