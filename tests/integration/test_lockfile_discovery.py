"""Integration tests for lockfile discovery against real git repositories.

Issue #79: these tests exercise :func:`discover_tracked_lockfiles` through
the real subprocess runner and real temporary directories, without stubbing
the internals, so the git and filesystem integrations are covered rather
than mocked layouts.
"""

from __future__ import annotations

import shutil
import subprocess
import typing as typ

import pytest

from lading.commands import lockfile
from lading.runtime import subprocess_runner

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(60)


_GIT_EXECUTABLE = shutil.which("git") or "/usr/bin/git"


def _git(repo: Path, *args: str) -> None:
    """Run a git command in ``repo`` and fail loudly on error."""
    subprocess.run(  # noqa: S603 - fixed argv, test fixture setup
        (_GIT_EXECUTABLE, *args),
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
        },
    )


def _init_repo(repo: Path) -> None:
    """Initialise a git repository with deterministic identity settings."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "--quiet")


def _add_crate(repo: Path, relative: str, *, with_manifest: bool = True) -> Path:
    """Create a crate directory with a Cargo.lock (and optional manifest)."""
    crate_dir = repo / relative if relative else repo
    crate_dir.mkdir(parents=True, exist_ok=True)
    lock = crate_dir / "Cargo.lock"
    lock.write_text("# lock\n", encoding="utf-8")
    if with_manifest:
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
    return lock


def test_discovery_returns_tracked_lockfiles_with_manifests(tmp_path: Path) -> None:
    """Tracked lockfiles with adjacent manifests are discovered for real."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    root_lock = _add_crate(repo, "")
    nested_lock = _add_crate(repo, "crates/alpha")
    _add_crate(repo, "no-manifest", with_manifest=False)
    target_lock = _add_crate(repo, "target/debug")
    _git(repo, "add", "-A")

    result = lockfile.discover_tracked_lockfiles(repo, subprocess_runner)

    assert set(result) == {root_lock, nested_lock}
    assert target_lock not in result


def test_discovery_ignores_untracked_lockfiles(tmp_path: Path) -> None:
    """Lockfiles not in the git index are not discovered."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    tracked = _add_crate(repo, "tracked")
    _git(repo, "add", "tracked")
    untracked = _add_crate(repo, "untracked")

    result = lockfile.discover_tracked_lockfiles(repo, subprocess_runner)

    assert tracked in result
    assert untracked not in result


def test_discovery_raises_for_non_git_directory(tmp_path: Path) -> None:
    """A plain directory raises the typed not-a-repository error."""
    workspace = tmp_path / "plain"
    _add_crate(workspace, "")

    with pytest.raises(lockfile.NotAGitRepositoryError):
        lockfile.discover_tracked_lockfiles(workspace, subprocess_runner)
