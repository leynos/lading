"""Tests for manifest rewrite input errors."""

import pathlib
import re

import pytest

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig


@pytest.mark.parametrize(
    "content",
    ["[dependencies\n", "name = 1\nname = 2\n"],
    ids=["malformed-toml", "duplicate-key"],
)
def test_wraps_toml_errors_with_manifest_path(
    tmp_path: pathlib.Path,
    content: str,
) -> None:
    """TOML parsing failures identify the manifest being planned."""
    manifest = tmp_path / "fixture" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text(content, encoding="utf-8")
    group = ManifestRewriteConfig(paths=("fixture/Cargo.toml",))
    versions = bump_manifest_rewrites.ManifestRewriteVersions(
        updated_crate_names=frozenset({"alpha"}),
        pre_bump_versions={"alpha": "1.0.0"},
        target_version="2.0.0",
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match=re.escape(str(manifest.resolve())),
    ):
        bump_manifest_rewrites.plan_manifest_rewrites(
            tmp_path,
            (group,),
            (),
            versions,
        )
