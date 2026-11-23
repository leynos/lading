"""Tests for applying configured non-member manifest rewrites."""

import pathlib

import pytest

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)


def test_unchanged_manifest_is_omitted_and_dry_run_does_not_write(
    tmp_path: pathlib.Path,
) -> None:
    """No-op plans are empty and dry-run application preserves bytes."""
    content = '[dependencies]\nalpha = "2.0.0"\n'
    manifest = tmp_path / "fixture/Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text(content, encoding="utf-8")
    original_bytes = manifest.read_bytes()
    group = ManifestRewriteConfig(paths=("fixture/Cargo.toml",))

    assert not plan_fixture_rewrites(
        tmp_path,
        (group,),
        manifest_rewrite_versions(target_version="2.0.0"),
    ), "an unchanged target version must produce no rewrite plan"
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(), content, '[dependencies]\nalpha = "3.0.0"\n'
    )

    changed_paths = bump_manifest_rewrites.apply_manifest_rewrites(
        (plan,), dry_run=True
    )

    assert changed_paths == (manifest.resolve(),), (
        "dry-run must return the planned manifest path"
    )
    assert manifest.read_bytes() == original_bytes, (
        "dry-run must preserve manifest bytes"
    )


def test_application_rejects_manifest_changed_after_planning(
    tmp_path: pathlib.Path,
) -> None:
    """Applying a stale plan must preserve the newer manifest content."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[dependencies]\nalpha = "1.0.0"\n', encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest,
        '[dependencies]\nalpha = "1.0.0"\n',
        '[dependencies]\nalpha = "2.0.0"\n',
    )
    manifest.write_text('[dependencies]\nalpha = "local-edit"\n', encoding="utf-8")

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="changed after planning",
    ) as failure:
        bump_manifest_rewrites.apply_manifest_rewrites((plan,), dry_run=False)

    assert failure.value.failed_path == manifest, (
        "the error should identify the stale plan's target"
    )
    assert manifest.read_text(encoding="utf-8") == (
        '[dependencies]\nalpha = "local-edit"\n'
    ), "application must not overwrite changes made after planning"
