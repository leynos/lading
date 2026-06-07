"""Unit tests for Cargo lockfile helper functions."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

import pytest

from lading.commands import lockfile

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_discover_tracked_lockfiles_returns_empty_result(tmp_path: Path) -> None:
    """Empty git output produces no lockfiles."""
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == ("git", "ls-files", "*/Cargo.lock", "Cargo.lock")
        assert cwd == tmp_path
        return 0, "", ""

    assert lockfile.discover_tracked_lockfiles(tmp_path, runner) == ()


def test_discover_tracked_lockfiles_filters_missing_manifests(tmp_path: Path) -> None:
    """Only tracked lockfiles next to Cargo.toml files are returned."""
    root_manifest = tmp_path / "Cargo.toml"
    root_manifest.write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    nested = tmp_path / "tests" / "ui_lints"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (nested / "Cargo.lock").write_text("", encoding="utf-8")
    target = tmp_path / "target" / "debug"
    target.mkdir(parents=True)
    (target / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (target / "Cargo.lock").write_text("", encoding="utf-8")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return (
            0,
            (
                "Cargo.lock\n"
                "tests/ui_lints/Cargo.lock\n"
                "target/debug/Cargo.lock\n"
                "orphan/Cargo.lock\n"
            ),
            "",
        )

    assert lockfile.discover_tracked_lockfiles(tmp_path, runner) == (
        tmp_path / "Cargo.lock",
        nested / "Cargo.lock",
    )


def test_discover_tracked_lockfiles_handles_non_git_directory(
    tmp_path: Path,
) -> None:
    """Non-git workspaces do not abort lockfile discovery."""
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 128, "", "fatal: not a git repository"

    assert lockfile.discover_tracked_lockfiles(tmp_path, runner) == ()


def test_refresh_lockfile_returns_lockfile_path(tmp_path: Path) -> None:
    """Successful lockfile refresh returns the expected Cargo.lock path."""
    manifest = tmp_path / "Cargo.toml"

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == (
            "cargo",
            "generate-lockfile",
            "--manifest-path",
            str(manifest),
        )
        assert cwd == manifest.parent
        return 0, "", ""

    assert lockfile.refresh_lockfile(manifest, runner) == tmp_path / "Cargo.lock"


def test_refresh_lockfile_raises_on_failure(tmp_path: Path) -> None:
    """Refresh failures include cargo stderr in the raised error."""
    manifest = tmp_path / "Cargo.toml"

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 101, "", "failed to resolve"

    with pytest.raises(lockfile.LockfileRefreshError, match="failed to resolve"):
        lockfile.refresh_lockfile(manifest, runner)


def _validate_lockfile_freshness_for_exit_code(tmp_path: Path, exit_code: int) -> bool:
    """Run lockfile freshness validation with a fake cargo exit code."""
    manifest = tmp_path / "Cargo.toml"

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        assert command == (
            "cargo",
            "metadata",
            "--locked",
            "--manifest-path",
            str(manifest),
            "--format-version=1",
        )
        assert cwd == manifest.parent
        return exit_code, "", "stale"

    return lockfile.validate_lockfile_freshness(manifest, runner)


def test_validate_lockfile_freshness_returns_true_for_success(tmp_path: Path) -> None:
    """Cargo metadata success means the lockfile is fresh."""
    assert _validate_lockfile_freshness_for_exit_code(tmp_path, 0) is True


def test_validate_lockfile_freshness_returns_false_for_failure(tmp_path: Path) -> None:
    """Cargo metadata failure means the lockfile is stale."""
    assert _validate_lockfile_freshness_for_exit_code(tmp_path, 101) is False
