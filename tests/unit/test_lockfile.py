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
        """Stub runner returning a successful git result with empty stdout."""
        assert command == ("git", "ls-files", "**/Cargo.lock", "Cargo.lock")
        assert cwd == tmp_path
        return 0, "", ""

    result = lockfile.discover_tracked_lockfiles(tmp_path, runner)
    assert result == (), (
        "git repo with no tracked lockfiles should return an empty tuple; "
        f"got {result!r}"
    )


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
        assert command == ("git", "ls-files", "**/Cargo.lock", "Cargo.lock")
        assert cwd == tmp_path
        assert env is None
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

    result = lockfile.discover_tracked_lockfiles(tmp_path, runner)
    expected = (
        tmp_path / "Cargo.lock",
        nested / "Cargo.lock",
    )
    assert result == expected, (
        "only manifest-adjacent, non-target lockfiles should be returned; "
        f"expected {expected!r}, got {result!r}"
    )


def test_discover_tracked_lockfiles_accepts_manifest_probe(
    tmp_path: Path,
) -> None:
    """Manifest filtering is delegated to the injected probe."""
    probed: list[Path] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 0, "Cargo.lock\nnested/Cargo.lock\n", ""

    def manifest_exists(manifest_path: Path) -> bool:
        probed.append(manifest_path)
        return (
            manifest_path.name == "Cargo.toml" and manifest_path.parent.name != "nested"
        )

    result = lockfile.discover_tracked_lockfiles(
        tmp_path,
        runner,
        manifest_exists=manifest_exists,
    )

    assert result == (tmp_path / "Cargo.lock",)
    assert probed == [tmp_path / "Cargo.toml", tmp_path / "nested" / "Cargo.toml"]


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

    result = lockfile.discover_tracked_lockfiles(tmp_path, runner)
    assert result == (), (
        "discovery should not abort on non-git errors; "
        f"expected empty tuple, got {result!r}"
    )


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

    expected = tmp_path / "Cargo.lock"
    result = lockfile.refresh_lockfile(manifest, runner)
    assert result == expected, (
        "refresh helper returned unexpected lockfile path; "
        f"expected {expected!r}, got {result!r}"
    )


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


@pytest.mark.parametrize("case", [(0, True), (101, False)])
def test_validate_lockfile_freshness_parametrized(
    tmp_path: Path,
    case: tuple[int, bool],
) -> None:
    """Cargo metadata exit status determines whether the lockfile is fresh."""
    exit_code, expected_bool = case
    actual = _validate_lockfile_freshness_for_exit_code(tmp_path, exit_code)
    assert actual is expected_bool, (
        "freshness result did not match cargo metadata exit code; "
        f"exit_code={exit_code}, expected {expected_bool}, got {actual}"
    )
