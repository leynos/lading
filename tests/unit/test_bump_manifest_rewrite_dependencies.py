"""Tests for Cargo dependency matching in manifest rewrites."""

import pathlib

from tomlkit import parse as parse_toml

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig


def test_explicit_package_name_overrides_a_matching_dependency_alias(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit package name determines which crate the alias denotes."""
    manifest = tmp_path / "fixtures/standalone/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '[dependencies]\nrstest-bdd = { package = "serde", version = "0.6.0-beta4" }\n',
        encoding="utf-8",
    )
    plans = bump_manifest_rewrites.plan_manifest_rewrites(
        tmp_path,
        (ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",)),),
        (),
        bump_manifest_rewrites.ManifestRewriteVersions(
            updated_crate_names=frozenset({"rstest-bdd"}),
            pre_bump_versions={"rstest-bdd": "0.6.0-beta4"},
            target_version="0.6.0",
        ),
    )

    assert not plans, "a non-workspace package alias must not be rewritten"
    document = parse_toml(manifest.read_text(encoding="utf-8"))
    assert document["dependencies"]["rstest-bdd"]["version"] == "0.6.0-beta4", (
        "the key must not override an explicit package name"
    )
