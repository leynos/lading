"""Tests for recursive configured manifest rewrite glob traversal."""

import collections.abc as cabc
import pathlib
import sys

import pytest

from lading.commands import bump_manifest_rewrites, manifest_paths

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


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


def test_recursive_glob_skips_directory_junctions(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-follow recursion skips junctions while retaining real directories."""
    manifest = write_fixture_manifest(
        tmp_path,
        "fixtures/real/Cargo.toml",
        '[dependencies]\nalpha = "1.0.0"\n',
    )
    junction = manifest.parent.parent / "junction"
    junction.mkdir()
    original_is_junction = pathlib.Path.is_junction
    monkeypatch.setattr(
        pathlib.Path,
        "is_junction",
        lambda path: path == junction or original_is_junction(path),
    )

    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        "fixtures/**/Cargo.toml",
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert matches == (manifest,), (
        "recursive traversal must skip the simulated junction directory"
    )


def test_explicit_glob_path_through_junction_keeps_containment_check(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selected junction path is followed and rejected when it escapes."""
    workspace_root = tmp_path / "workspace"
    junction = workspace_root / "fixtures" / "external"
    junction.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Cargo.toml").write_text('[package]\nname = "outside"\n')
    junction.symlink_to(outside, target_is_directory=True)
    original_is_junction = pathlib.Path.is_junction
    monkeypatch.setattr(
        pathlib.Path,
        "is_junction",
        lambda path: path == junction or original_is_junction(path),
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="stay within the workspace",
    ):
        manifest_paths.expand_manifest_glob_candidates(
            workspace_root,
            "fixtures/external/Cargo.toml",
            error_type=bump_manifest_rewrites.ManifestRewriteError,
            label="Manifest rewrite",
        )


def test_terminal_recursive_glob_rejects_escaping_symlinks(
    tmp_path: pathlib.Path,
) -> None:
    """A terminal ``**`` match still checks symlink containment."""
    workspace_root = tmp_path / "workspace"
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    escape = workspace_root / "fixtures" / "escape"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="stay within the workspace",
    ):
        manifest_paths.expand_manifest_glob_candidates(
            workspace_root,
            "fixtures/**",
            error_type=bump_manifest_rewrites.ManifestRewriteError,
            label="Manifest rewrite",
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


@pytest.mark.parametrize(
    ("pattern", "fixture_paths"),
    [
        pytest.param(
            "fixtures/**/**/Cargo.toml",
            ("fixtures/a/Cargo.toml", "fixtures/a/deep/Cargo.toml"),
            id="adjacent-recursive-segments",
        ),
        pytest.param(
            "**/foo/**/Cargo.toml",
            ("foo/foo/Cargo.toml",),
            id="nonadjacent-recursive-segments",
        ),
    ],
)
def test_recursive_globs_do_not_duplicate_manifests(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
    pattern: str,
    fixture_paths: tuple[str, ...],
) -> None:
    """Different ``**`` decompositions return each selected path once."""
    expected = tuple(
        sorted(
            write_fixture_manifest(tmp_path, path, '[dependencies]\nalpha = "1.0.0"\n')
            for path in fixture_paths
        )
    )
    matches = manifest_paths.expand_manifest_glob_candidates(
        tmp_path,
        pattern,
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
    )

    assert matches == expected, "recursive glob segments must not duplicate paths"


def test_recursive_glob_handles_deep_directory_chains(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A deep workspace tree does not consume Python call-stack depth."""
    workspace_root = tmp_path / "workspace"
    relative_path = "/".join((*(("a",) * 300), "Cargo.toml"))
    manifest = write_fixture_manifest(
        workspace_root, relative_path, '[dependencies]\nalpha = "1.0.0"\n'
    )
    assert len(str(manifest)) < 1_024, "the deep path must fit common path limits"

    frame = sys._getframe()
    frame_depth = 0
    while frame is not None:
        frame_depth += 1
        frame = frame.f_back
    original_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(frame_depth + 150)
        matches = manifest_paths.expand_manifest_glob_candidates(
            workspace_root,
            "**/Cargo.toml",
            error_type=bump_manifest_rewrites.ManifestRewriteError,
            label="Manifest rewrite",
        )
    finally:
        sys.setrecursionlimit(original_limit)

    assert matches == (manifest.resolve(),), (
        "recursive traversal should find the manifest beyond recursion depth"
    )


def test_recursive_glob_skips_repeated_candidate_pattern_states(
    tmp_path: pathlib.Path,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """Repeated recursive decompositions scan each state only once."""
    relative_path = "/".join((*(("a",) * 20), "Cargo.toml"))
    manifest = write_fixture_manifest(
        tmp_path, relative_path, '[dependencies]\nalpha = "1.0.0"\n'
    )
    pattern = "/".join((*(("**", "a") * 10), "**", "Cargo.toml"))
    directory_reads = 0

    def read_directory(directory: pathlib.Path) -> tuple[pathlib.Path, ...]:
        """Count scans while preserving the resolver's normal directory order."""
        nonlocal directory_reads
        directory_reads += 1
        return tuple(sorted(directory.iterdir()))

    traversal = manifest_paths._ManifestGlobTraversal(
        workspace_root=tmp_path,
        pattern=pattern,
        error_type=bump_manifest_rewrites.ManifestRewriteError,
        label="Manifest rewrite",
        directory_reader=read_directory,
    )

    assert traversal.collect() == (manifest.resolve(),), (
        "overlapping recursive segments must retain the matching manifest"
    )
    assert directory_reads < 1_000, (
        "the same lexical path and pattern index should not be scanned twice"
    )
