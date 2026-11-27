"""Tests for configured manifest rewrite path resolution."""

import collections.abc as cabc
import pathlib

import pytest

from lading.commands import (
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrites,
)
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.cwd import chdir_for_test
from tests.helpers.manifest_rewrites import plan_fixture_rewrites

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


def test_resolves_globs_deduplicates_and_merges_groups(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Glob matches are sorted and overlapping groups merge their rules."""
    first = write_fixture_manifest(
        tmp_path,
        "fixtures/a/Cargo.toml",
        '[dependencies]\nalpha = "^1.0.0"\n[metadata]\npath = "alpha-1.0.0"\n',
    )
    second = write_fixture_manifest(
        tmp_path, "fixtures/b/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    groups = (
        ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),
        ManifestRewriteConfig(
            paths=("fixtures/a/Cargo.toml",),
            dependencies=False,
            string_values=(StringValueRewriteConfig(table=("metadata",)),),
        ),
    )

    plans = plan_fixture_rewrites(tmp_path, groups)

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


def test_existing_path_with_glob_metacharacters_is_treated_as_literal(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """An existing bracketed path takes precedence over glob expansion."""
    literal = write_fixture_manifest(
        tmp_path,
        "fixtures/[dev]/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )
    write_fixture_manifest(
        tmp_path,
        "fixtures/d/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )

    plans = plan_fixture_rewrites(
        tmp_path,
        (ManifestRewriteConfig(paths=("fixtures/[dev]/Cargo.toml",)),),
    )

    assert [plan.path for plan in plans] == [literal.resolve()], (
        "glob metacharacters in an existing manifest path must not select siblings"
    )


def test_unconfigured_rewrites_read_no_sources(tmp_path: pathlib.Path) -> None:
    """An empty rewrite configuration performs no filesystem reads."""
    sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
        tmp_path, (), ()
    )

    assert not sources, "no configured rewrites must yield no sources"


def test_relative_root_candidates_resolve_to_absolute_paths(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Sources remain absolute when the configured root is relative."""
    manifest = write_fixture_manifest(
        tmp_path,
        "fixtures/demo/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )
    chdir_for_test(monkeypatch, tmp_path.parent)
    relative_root = pathlib.Path(tmp_path.name)

    (source,) = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
        relative_root,
        (ManifestRewriteConfig(paths=("fixtures/demo/Cargo.toml",)),),
        (),
    )

    assert source.path == manifest.resolve(), (
        "a relative workspace root must still produce an absolute source path"
    )


def test_configured_path_outside_the_workspace_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """A parent-traversing configured path cannot be selected."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside" / "Cargo.toml"
    outside.parent.mkdir()
    outside.write_text('[dependencies]\nalpha = "1.0.0"\n', encoding="utf-8")

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match=r"must name a regular Cargo\.toml file",
    ):
        plan_fixture_rewrites(
            workspace_root,
            (ManifestRewriteConfig(paths=("../outside/Cargo.toml",)),),
        )


def test_configured_path_must_name_a_cargo_manifest(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A selected file that is not named ``Cargo.toml`` is rejected."""
    write_fixture_manifest(
        tmp_path,
        "fixtures/other.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match=r"must point to a Cargo\.toml file",
    ):
        plan_fixture_rewrites(
            tmp_path,
            (ManifestRewriteConfig(paths=("fixtures/other.toml",)),),
        )


def test_workspace_owned_manifests_are_skipped(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Root and member manifests remain owned by the member stage."""
    root_manifest = write_fixture_manifest(
        tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/alpha"]\n'
    )
    member_manifest = write_fixture_manifest(
        tmp_path,
        "crates/alpha/Cargo.toml",
        '[package]\nname = "alpha"\nversion = "0.1.0"\n',
    )

    sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
        tmp_path,
        (
            ManifestRewriteConfig(
                paths=("Cargo.toml", "crates/alpha/Cargo.toml"),
            ),
        ),
        (member_manifest,),
    )

    assert not sources, (
        "workspace-owned manifests must not be planned as fixture rewrites"
    )
    assert root_manifest.exists(), "the root manifest must remain in place"


def test_unreadable_manifest_is_reported(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A manifest that is not valid UTF-8 is reported with its path."""
    manifest = write_fixture_manifest(
        tmp_path, "fixtures/a/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    manifest.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not read manifest rewrite target",
    ):
        plan_fixture_rewrites(
            tmp_path,
            (ManifestRewriteConfig(paths=("fixtures/a/Cargo.toml",)),),
        )


def test_empty_glob_is_rejected(tmp_path: pathlib.Path) -> None:
    """A configured glob that matches no manifest stops planning."""
    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Manifest rewrite glob matched no files",
    ):
        plan_fixture_rewrites(
            tmp_path,
            (ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),),
        )


def test_glob_rejects_an_escaping_directory_symlink(
    tmp_path: pathlib.Path,
) -> None:
    """A glob cannot follow a symlink that leaves the workspace."""
    workspace_root = tmp_path / "workspace"
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "Cargo.toml").write_text(
        '[package]\nname = "outside"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    alias = workspace_root / "fixtures" / "escape"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not expand manifest rewrite glob",
    ):
        plan_fixture_rewrites(
            workspace_root,
            (ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),),
        )
