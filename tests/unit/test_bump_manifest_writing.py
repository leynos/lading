"""Unit tests for bump manifest rendering and dependency-section rewrites."""

from pathlib import Path

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump_manifests, bump_toml
from tests.helpers.workspace_builders import _load_version


def test_render_manifest_returns_updated_text(tmp_path: Path) -> None:
    """Rendering applies the new version to the selected table."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    content = bump_manifests.render_manifest(
        manifest_path,
        (("package",),),
        "1.0.0",
        bump_manifests.ManifestRenderOptions(),
    )
    assert content is not None, "changing the version should produce new text"
    manifest_path.write_text(content, encoding="utf-8")
    assert _load_version(manifest_path, ("package",)) == "1.0.0", (
        "the rendered manifest should carry the new package version"
    )


def test_render_manifest_preserves_inline_comment(tmp_path: Path) -> None:
    """Inline comments survive manifest rendering."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nversion = "0.1.0"  # keep me\n', encoding="utf-8"
    )
    text = bump_manifests.render_manifest(
        manifest_path,
        (("package",),),
        "1.2.3",
        bump_manifests.ManifestRenderOptions(),
    )
    assert text is not None, "rewriting the version should produce new text"
    assert "# keep me" in text, "the inline comment should survive the rewrite"
    document = parse_toml(text)
    assert document["package"]["version"] == "1.2.3", (
        "package version should be rewritten to the target"
    )


def test_render_manifest_returns_none_when_unchanged(tmp_path: Path) -> None:
    """No text is produced when the manifest already records the target version."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original)
    content = bump_manifests.render_manifest(
        manifest_path,
        (("package",),),
        "0.1.0",
        bump_manifests.ManifestRenderOptions(),
    )
    assert content is None, "an already-current version should produce no text"
    assert manifest_path.read_text() == original, (
        "the manifest file should be left byte-for-byte unchanged"
    )


@pytest.mark.parametrize(
    ("include_workspace_sections", "expected_workspace_alpha"),
    [(True, "^1.0.0"), (False, "^0.1.0")],
    ids=["with_workspace_flag", "without_workspace_flag"],
)
def test_update_dependency_sections_workspace_flag(
    *,
    include_workspace_sections: bool,
    expected_workspace_alpha: str,
) -> None:
    """[workspace.dependencies] updates only when the flag is set."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump_toml.update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=include_workspace_sections,
    )
    assert changed is True, "updating [dependencies] should report a change"
    assert document["dependencies"]["alpha"].value == "1.0.0", (
        "[dependencies] alpha should always be rewritten"
    )
    assert (
        document["workspace"]["dependencies"]["alpha"].value == expected_workspace_alpha
    ), "[workspace.dependencies] alpha should update only with the workspace flag"


def test_update_dependency_sections_workspace_only() -> None:
    """When only workspace sections exist, they are updated with the flag."""
    document = parse_toml('[workspace.dependencies]\nalpha = "0.1.0"\n')
    changed = bump_toml.update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "2.0.0",
        include_workspace_sections=True,
    )
    assert changed is True, "updating a workspace-only section should report a change"
    assert document["workspace"]["dependencies"]["alpha"].value == "2.0.0", (
        "[workspace.dependencies] alpha should be rewritten to the target"
    )


def test_update_dependency_sections_workspace_dev_and_build() -> None:
    """Workspace dev-dependencies and build-dependencies are updated."""
    document = parse_toml(
        '[workspace.dev-dependencies]\nalpha = "~0.1.0"\n\n'
        '[workspace.build-dependencies]\nbeta = { version = "0.1.0" }\n'
    )
    changed = bump_toml.update_dependency_sections(
        document,
        {"dev-dependencies": ("alpha",), "build-dependencies": ("beta",)},
        "3.0.0",
        include_workspace_sections=True,
    )
    assert changed is True, "updating dev/build sections should report a change"
    assert document["workspace"]["dev-dependencies"]["alpha"].value == "~3.0.0", (
        "[workspace.dev-dependencies] alpha should be rewritten"
    )
    build_deps = document["workspace"]["build-dependencies"]
    assert build_deps["beta"]["version"].value == "3.0.0", (
        "[workspace.build-dependencies] beta.version should be rewritten"
    )


def test_package_alias_does_not_rewrite_a_workspace_dependency(
    tmp_path: Path,
) -> None:
    """A package alias and an unrelated workspace dependency stay separate."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[workspace.dependencies]\n"
        'shared = { package = "serde", version = "1.0.0" }\n\n'
        "[dependencies]\n"
        'shared = { package = "alpha", path = "crates/alpha", version = "0.1.0" }\n',
        encoding="utf-8",
    )

    content = bump_manifests.render_manifest(
        manifest_path,
        (),
        "1.2.3",
        bump_manifests.ManifestRenderOptions(
            dependency_sections={"dependencies": {"shared"}},
            workspace_sections={"dependencies": {"alpha"}},
        ),
    )

    assert content is not None, "the local dependency version must change"
    document = parse_toml(content)
    assert document["dependencies"]["shared"]["version"] == "1.2.3", (
        "the aliased local dependency must be updated"
    )
    assert document["workspace"]["dependencies"]["shared"]["version"] == "1.0.0", (
        "the unrelated workspace dependency must keep its requirement"
    )
