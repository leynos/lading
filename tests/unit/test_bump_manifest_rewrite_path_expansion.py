"""Expand configured rewrite paths and report inspection failures."""

import collections.abc as cabc
import errno
import os
import pathlib

import pytest

from lading.commands import (
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrites,
    manifest_paths,
)
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import plan_fixture_rewrites

type _ManifestWriter = cabc.Callable[[pathlib.Path, str, str], pathlib.Path]


def test_unconfigured_rewrites_read_no_sources(tmp_path: pathlib.Path) -> None:
    """An empty rewrite configuration performs no filesystem reads."""
    sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
        tmp_path, (), ()
    )

    assert not sources, "no configured rewrites must yield no sources"


def test_glob_scan_failure_is_reported(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing glob traversal becomes a manifest rewrite error."""

    def fail_scan(*_args: object, **_kwargs: object) -> tuple[pathlib.Path, ...]:
        """Simulate a directory scan failure."""
        message = "scan failed"
        raise OSError(message)

    monkeypatch.setattr(manifest_paths, "expand_manifest_glob_candidates", fail_scan)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not expand manifest rewrite glob",
    ):
        plan_fixture_rewrites(
            tmp_path,
            (ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),),
        )


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


def test_windows_invalid_literal_name_falls_back_to_glob(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_fixture_manifest: _ManifestWriter,
) -> None:
    """A Windows invalid-name stat failure still reaches glob expansion."""
    manifest = write_fixture_manifest(
        tmp_path, "fixtures/a/Cargo.toml", '[dependencies]\nalpha = "1.0.0"\n'
    )
    literal_candidate = tmp_path / "fixtures/*/Cargo.toml"
    original_lstat = pathlib.Path.lstat

    def reject_windows_invalid_name(path: pathlib.Path) -> os.stat_result:
        """Simulate Windows rejecting glob metacharacters in a filename."""
        if path == literal_candidate:
            error = OSError(errno.EINVAL, "invalid filename")
            vars(error)["winerror"] = 123
            raise error
        return original_lstat(path)

    monkeypatch.setattr(pathlib.Path, "lstat", reject_windows_invalid_name)

    plans = plan_fixture_rewrites(
        tmp_path,
        (ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),),
    )

    assert [plan.path for plan in plans] == [manifest.resolve()], (
        "an invalid literal name must fall back to glob expansion"
    )


def test_unexpected_literal_inspection_failure_is_reported(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Windows stat failures still stop with a rewrite error."""
    literal_candidate = tmp_path / "fixtures/*/Cargo.toml"
    original_lstat = pathlib.Path.lstat

    def reject_inspection(path: pathlib.Path) -> os.stat_result:
        """Fail the literal probe with a non-Windows I/O error."""
        if path == literal_candidate:
            raise PermissionError(errno.EACCES, "permission denied")
        return original_lstat(path)

    monkeypatch.setattr(pathlib.Path, "lstat", reject_inspection)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not inspect literal",
    ):
        plan_fixture_rewrites(
            tmp_path,
            (ManifestRewriteConfig(paths=("fixtures/*/Cargo.toml",)),),
        )
