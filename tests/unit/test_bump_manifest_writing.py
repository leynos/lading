"""Unit tests for bump manifest writing and dependency-section rewrites."""

from __future__ import annotations

from pathlib import Path

from tomlkit import parse as parse_toml

from lading.commands import bump
from tests.helpers.workspace_builders import _load_version


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


def test_update_dependency_sections_with_workspace_flag() -> None:
    """The include_workspace_sections flag updates [workspace.dependencies]."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["dependencies"]["alpha"].value == "1.0.0"
    assert document["workspace"]["dependencies"]["alpha"].value == "^1.0.0"


def test_update_dependency_sections_without_workspace_flag() -> None:
    """Without the flag, [workspace.dependencies] is not updated."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=False,
    )
    assert changed is True
    assert document["dependencies"]["alpha"].value == "1.0.0"
    # workspace.dependencies should remain unchanged
    assert document["workspace"]["dependencies"]["alpha"].value == "^0.1.0"


def test_update_dependency_sections_workspace_only() -> None:
    """When only workspace sections exist, they are updated with the flag."""
    document = parse_toml('[workspace.dependencies]\nalpha = "0.1.0"\n')
    changed = bump._update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "2.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["workspace"]["dependencies"]["alpha"].value == "2.0.0"


def test_update_dependency_sections_workspace_dev_and_build() -> None:
    """Workspace dev-dependencies and build-dependencies are updated."""
    document = parse_toml(
        '[workspace.dev-dependencies]\nalpha = "~0.1.0"\n\n'
        '[workspace.build-dependencies]\nbeta = { version = "0.1.0" }\n'
    )
    changed = bump._update_dependency_sections(
        document,
        {"dev-dependencies": ("alpha",), "build-dependencies": ("beta",)},
        "3.0.0",
        include_workspace_sections=True,
    )
    assert changed is True
    assert document["workspace"]["dev-dependencies"]["alpha"].value == "~3.0.0"
    build_deps = document["workspace"]["build-dependencies"]
    assert build_deps["beta"]["version"].value == "3.0.0"
