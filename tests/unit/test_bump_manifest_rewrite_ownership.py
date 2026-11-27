"""Tests for the workspace-member ownership boundary of manifest rewrites."""

import collections.abc as cabc
import logging
import pathlib
import typing as typ

from lading.commands import bump_manifest_rewrite_filesystem
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import plan_fixture_rewrites

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]

if typ.TYPE_CHECKING:
    import pytest


def test_skips_workspace_and_member_manifests(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Configured root and member paths remain owned by the regular stages."""
    root_manifest = write_fixture_manifest(
        tmp_path, "Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    member_manifest = write_fixture_manifest(
        tmp_path, "crates/alpha/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    group = ManifestRewriteConfig(paths=("Cargo.toml", "crates/alpha/Cargo.toml"))

    with caplog.at_level(
        logging.DEBUG, logger=bump_manifest_rewrite_filesystem.__name__
    ):
        plans = plan_fixture_rewrites(tmp_path, (group,), members=(member_manifest,))

    assert not plans, "workspace and member manifests must be skipped"
    assert [record.getMessage() for record in caplog.records] == [
        (
            "Skipping configured manifest rewrite Cargo.toml because it is a "
            f"workspace-owned manifest at {root_manifest.resolve()}"
        ),
        (
            "Skipping configured manifest rewrite crates/alpha/Cargo.toml because "
            f"it is a workspace-owned manifest at {member_manifest.resolve()}"
        ),
    ], "skip logs must identify both the configured and resolved path"
    assert root_manifest.read_text(encoding="utf-8").endswith('alpha = "1.0.0"\n'), (
        "the rewrite planner must leave the root manifest to its owner stage"
    )
