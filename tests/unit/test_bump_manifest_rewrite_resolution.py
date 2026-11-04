"""Tests for configured manifest rewrite path resolution."""

import collections.abc as cabc
import logging
import pathlib

import pytest

from lading.commands import bump_manifest_rewrites
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    plan_fixture_rewrites,
)

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


def test_resolves_globs_deduplicates_and_merges_groups(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Glob matches are sorted and overlapping groups merge their rules."""
    root = tmp_path
    write_fixture_manifest(root, "Cargo.toml", "[workspace]\nmembers = []\n")
    first = write_fixture_manifest(
        root,
        "fixtures/a/Cargo.toml",
        '[dependencies]\nalpha = "^1.0.0"\n[metadata]\npath = "alpha-1.0.0"\n',
    )
    second = write_fixture_manifest(
        root, "fixtures/b/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    groups = (
        ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),
        ManifestRewriteConfig(
            paths=("fixtures/a/Cargo.toml",),
            dependencies=False,
            string_values=(StringValueRewriteConfig(table=("metadata",)),),
        ),
    )

    plans = plan_fixture_rewrites(root, groups)

    assert [plan.path for plan in plans] == [first.resolve(), second.resolve()], (
        "glob expansion must de-duplicate paths and preserve sorted candidates"
    )
    assert 'alpha = "^2.0.0"' in plans[0].new_text, (
        "the first selected dependency must use the target version"
    )
    assert 'path = "alpha-2.0.0"' in plans[0].new_text, (
        "the selected string value must use the target version"
    )
    assert 'alpha = "2.0.0"' in plans[1].new_text, (
        "the second glob match must receive dependency rewrites"
    )


@pytest.mark.parametrize(
    ("configured_path", "message"),
    [
        ("missing/Cargo.toml", "regular file"),
        ("missing/*.toml", "matched no files"),
        ("fixtures/not-a-manifest.toml", "Cargo.toml file"),
        ("../outside/Cargo.toml", "stay within the workspace"),
    ],
)
def test_rejects_invalid_configured_paths(
    tmp_path: pathlib.Path,
    configured_path: str,
    message: str,
) -> None:
    """Missing, unmatched, malformed, and escaping paths fail during planning."""
    group = ManifestRewriteConfig(paths=(configured_path,))

    with pytest.raises(bump_manifest_rewrites.ManifestRewriteError, match=message):
        plan_fixture_rewrites(tmp_path, (group,))


def test_rejects_glob_matches_that_are_not_regular_files(
    tmp_path: pathlib.Path,
) -> None:
    """A directory named Cargo.toml cannot pass candidate validation."""
    (tmp_path / "fixtures" / "Cargo.toml").mkdir(parents=True)
    group = ManifestRewriteConfig(paths=("fixtures/**/Cargo.toml",))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError, match="regular file"
    ):
        plan_fixture_rewrites(tmp_path, (group,))


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

    with caplog.at_level(logging.DEBUG, logger=bump_manifest_rewrites.__name__):
        plans = plan_fixture_rewrites(tmp_path, (group,), members=(member_manifest,))

    assert not plans, "workspace and member manifests must be skipped"
    assert [record.getMessage() for record in caplog.records] == [
        (
            "Skipping configured manifest rewrite Cargo.toml because it resolves to "
            f"a workspace-owned manifest at {root_manifest.resolve()}"
        ),
        (
            "Skipping configured manifest rewrite crates/alpha/Cargo.toml because it "
            f"resolves to a workspace-owned manifest at {member_manifest.resolve()}"
        ),
    ], "skip logs must identify both the configured and resolved path"
    assert root_manifest.read_text(encoding="utf-8").endswith('alpha = "1.0.0"\n'), (
        "the rewrite planner must leave the root manifest to its owner stage"
    )


def test_skips_missing_member_manifest_before_file_validation(
    tmp_path: pathlib.Path,
) -> None:
    """Configured member paths remain skipped even if the file is absent."""
    member_manifest = tmp_path / "crates/alpha/Cargo.toml"
    group = ManifestRewriteConfig(paths=("crates/alpha/Cargo.toml",))

    plans = plan_fixture_rewrites(tmp_path, (group,), members=(member_manifest,))

    assert not plans, "missing member manifests must be skipped before validation"
