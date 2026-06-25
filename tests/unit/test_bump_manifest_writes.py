"""Unit tests for bump manifest write/skip behaviour and crate updates."""

from __future__ import annotations

from pathlib import Path

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump
from tests.helpers.bump_builders import (
    UpdateCrateTestParams,
    _make_workspace_with_alpha_dependency,
    _parse_manifest_versions,
)
from tests.helpers.workspace_builders import _load_version, _make_config


def test_update_manifest_writes_when_changed(tmp_path: Path) -> None:
    """Applying a new version persists changes to disk."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    changed = bump._update_manifest(
        manifest_path, (("package",),), "1.0.0", bump.BumpOptions()
    )
    assert changed is True
    assert _load_version(manifest_path, ("package",)) == "1.0.0"


def test_update_manifest_preserves_inline_comment(tmp_path: Path) -> None:
    """Inline comments survive manifest rewrites."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nversion = "0.1.0"  # keep me\n', encoding="utf-8"
    )
    changed = bump._update_manifest(
        manifest_path, (("package",),), "1.2.3", bump.BumpOptions()
    )
    assert changed is True
    text = manifest_path.read_text(encoding="utf-8")
    assert "# keep me" in text
    document = parse_toml(text)
    assert document["package"]["version"] == "1.2.3"


def test_update_manifest_skips_when_unchanged(tmp_path: Path) -> None:
    """No write occurs when the manifest already records the target version."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original)
    changed = bump._update_manifest(
        manifest_path, (("package",),), "0.1.0", bump.BumpOptions()
    )
    assert changed is False
    assert manifest_path.read_text() == original


@pytest.mark.parametrize(
    "params",
    [
        UpdateCrateTestParams(
            test_id="excluded_crate_skips_version_bump",
            dependency_spec=("alpha", "0.1.0"),
            exclude_crates=("beta",),
            expected_package_version="0.1.0",
            expected_alpha_version="1.2.3",
        ),
        UpdateCrateTestParams(
            test_id="updates_version_and_dependencies",
            dependency_spec=("alpha", "^0.1.0"),
            exclude_crates=(),
            expected_package_version="1.2.3",
            expected_alpha_version="^1.2.3",
        ),
    ],
    ids=lambda p: p.test_id,
)
def test_update_crate_manifest(tmp_path: Path, params: UpdateCrateTestParams) -> None:
    """Crate manifest updates handle exclusions and dependency rewrites."""
    crate, workspace = _make_workspace_with_alpha_dependency(
        tmp_path,
        dependency=params.dependency_spec,
    )
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            configuration=_make_config(exclude=params.exclude_crates),
            workspace=workspace,
        ),
    )

    changed = bump._update_crate_manifest(
        crate,
        "1.2.3",
        context,
    )

    assert changed is True
    package_version, alpha_version = _parse_manifest_versions(crate.manifest_path)
    assert package_version == params.expected_package_version
    assert alpha_version == params.expected_alpha_version
