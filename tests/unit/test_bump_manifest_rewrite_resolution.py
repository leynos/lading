"""Tests for configured manifest rewrite path resolution."""

import collections.abc as cabc
import logging
import pathlib

import pytest

from lading.commands import (
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrites,
    manifest_paths,
)
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


def test_glob_skips_symlinked_files_before_traversing_remaining_segments(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A non-directory wildcard match cannot abort later valid manifest matches."""
    manifest = write_fixture_manifest(
        tmp_path,
        "fixtures/a/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )
    outside_file = tmp_path.parent / f"{tmp_path.name}-notice"
    outside_file.write_text("not a directory", encoding="utf-8")
    symlinked_file = tmp_path / "fixtures" / "000-notice"
    symlinked_file.symlink_to(outside_file)

    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        "fixtures/*/Cargo.toml",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert matches == (manifest,), (
        "a symlinked file must be skipped when more path segments remain"
    )


def test_glob_follows_internal_directory_symlinks_for_remaining_segments(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Non-recursive glob segments can traverse contained directory symlinks."""
    manifest = write_fixture_manifest(
        tmp_path,
        "fixtures/real/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )
    alias = tmp_path / "fixtures" / "alias"
    alias.symlink_to(manifest.parent, target_is_directory=True)

    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        "fixtures/*/Cargo.toml",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert tmp_path / "fixtures/alias/Cargo.toml" in matches, (
        "a contained directory symlink must remain eligible for non-recursive globs"
    )


def test_glob_rejects_escaping_directory_symlinks(
    tmp_path: pathlib.Path,
) -> None:
    """Following a glob directory symlink must still enforce containment."""
    outside_directory = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_directory.mkdir()
    (outside_directory / "Cargo.toml").write_text(
        '[package]\nname = "outside"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    alias = tmp_path / "fixtures" / "escape"
    alias.parent.mkdir()
    alias.symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="stay within the workspace",
    ):
        manifest_paths.expand_manifest_glob_candidates(
            tmp_path,
            "fixtures/*/Cargo.toml",
            error_type=bump_manifest_rewrites.ManifestRewriteError,
            label="Manifest rewrite",
        )


def test_recursive_globs_do_not_follow_directory_symlink_cycles(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Recursive ``**`` ignores a directory symlink back to an ancestor."""
    manifest = write_fixture_manifest(
        tmp_path,
        "fixtures/a/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )
    cycle = manifest.parent / "loop"
    cycle.symlink_to(tmp_path / "fixtures", target_is_directory=True)

    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        "fixtures/**/Cargo.toml",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert matches == (manifest,), (
        "recursive glob traversal must not revisit symlink cycles"
    )


def test_terminal_recursive_glob_includes_manifest_files(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A terminal ``**`` records matching files as well as directories."""
    manifest = write_fixture_manifest(
        tmp_path, "fixtures/demo/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )

    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        "fixtures/**",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert manifest in matches, "terminal recursive globs must retain file matches"


def test_adjacent_recursive_glob_segments_do_not_duplicate_manifests(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Adjacent ``**`` segments traverse each matching manifest once."""
    shallow = write_fixture_manifest(
        tmp_path, "fixtures/a/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    nested = write_fixture_manifest(
        tmp_path,
        "fixtures/a/deep/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )

    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        "fixtures/**/**/Cargo.toml",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert matches == (shallow, nested), (
        "adjacent recursive segments must not duplicate matching paths"
    )


def test_manifest_rewrite_preserves_crlf_line_endings(
    tmp_path: pathlib.Path,
) -> None:
    """Planning and applying a rewrite retain a fixture's CRLF convention."""
    manifest = tmp_path / "fixtures" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    original = b'[dependencies]\r\nalpha = "1.0.0"\r\n'
    manifest.write_bytes(original)
    groups = (ManifestRewriteConfig(paths=("fixtures/Cargo.toml",)),)

    (plan,) = plan_fixture_rewrites(tmp_path, groups)
    bump_manifest_rewrites.apply_manifest_rewrites((plan,), dry_run=False)

    assert manifest.read_bytes() == b'[dependencies]\r\nalpha = "2.0.0"\r\n', (
        "the rewritten manifest should retain CRLF line endings"
    )


def test_application_detects_line_ending_only_source_change(
    tmp_path: pathlib.Path,
) -> None:
    """A newline-only edit after planning invalidates the source snapshot."""
    manifest = tmp_path / "fixtures" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    original = b'[dependencies]\r\nalpha = "1.0.0"\r\n'
    manifest.write_bytes(original)
    groups = (ManifestRewriteConfig(paths=("fixtures/Cargo.toml",)),)
    plans = plan_fixture_rewrites(tmp_path, groups)
    manifest.write_bytes(original.replace(b"\r\n", b"\n"))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="changed after planning",
    ):
        bump_manifest_rewrites.apply_manifest_rewrites(plans, dry_run=False)

    assert manifest.read_bytes() == original.replace(b"\r\n", b"\n"), (
        "application should preserve the external line-ending edit"
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


def test_malformed_manifest_rewrite_error_identifies_its_path(
    tmp_path: pathlib.Path,
) -> None:
    """Malformed selected TOML reports the file that failed parsing."""
    manifest = tmp_path / "fixtures/standalone/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[dependencies\n", encoding="utf-8")
    group = ManifestRewriteConfig(paths=("fixtures/standalone/Cargo.toml",))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not parse manifest rewrite target",
    ) as failure:
        plan_fixture_rewrites(tmp_path, (group,))

    assert failure.value.failed_path == manifest.resolve(), (
        "parse errors must retain the resolved manifest path"
    )


@pytest.mark.parametrize(
    "pattern",
    ["fixtures/**/Cargo.toml", "fixtures/**/Cargo.toml/"],
    ids=["file-pattern", "directory-pattern"],
)
def test_rejects_glob_matches_that_are_not_regular_files(
    tmp_path: pathlib.Path,
    pattern: str,
) -> None:
    """A directory named Cargo.toml cannot pass candidate validation."""
    (tmp_path / "fixtures" / "Cargo.toml").mkdir(parents=True)
    group = ManifestRewriteConfig(paths=(pattern,))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError, match="regular file"
    ):
        plan_fixture_rewrites(tmp_path, (group,))


def test_trailing_slash_glob_does_not_select_regular_manifest_files(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A trailing separator keeps a manifest glob directory-only."""
    write_fixture_manifest(
        tmp_path, "fixtures/demo/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    group = ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml/",))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError, match="matched no files"
    ):
        plan_fixture_rewrites(tmp_path, (group,))


def test_propagates_directory_scan_failures_during_glob_expansion(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A partial glob result must not hide an unreadable selected subtree."""
    write_fixture_manifest(
        tmp_path, "fixtures/a/deep/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    denied_directory = tmp_path / "fixtures/z"
    denied_directory.mkdir(parents=True)

    def read_directory(path: pathlib.Path) -> tuple[pathlib.Path, ...]:
        """Raise a permission error when traversal reaches the denied directory."""
        if path == denied_directory:
            raise PermissionError
        return tuple(sorted(path.iterdir()))

    traversal = manifest_paths._ManifestGlobTraversal(
        workspace_root=tmp_path,
        pattern="fixtures/*/*/Cargo.toml",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
        directory_reader=read_directory,
    )

    with pytest.raises(PermissionError):
        traversal.collect()


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
