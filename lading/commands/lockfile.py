"""Cargo lockfile discovery, refresh, and freshness validation helpers."""

from __future__ import annotations

import logging
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from lading.commands.publish_execution import _CommandRunner

LOGGER = logging.getLogger(__name__)


class LockfileRefreshError(RuntimeError):
    """Raised when Cargo cannot regenerate a lockfile."""


def _query_git_tracked_lockfiles(
    workspace_root: Path,
    runner: _CommandRunner,
) -> str | None:
    """Run ``git ls-files`` and return stdout, or ``None`` on failure."""
    exit_code, stdout, stderr = runner(
        ("git", "ls-files", "*/Cargo.lock", "Cargo.lock"),
        cwd=workspace_root,
    )
    if exit_code == 0:
        return stdout
    detail = (stderr or stdout).strip()
    if "not a git repository" in detail.lower():
        LOGGER.warning(
            "Skipping Cargo.lock discovery because %s is not a git repository",
            workspace_root,
        )
    else:
        LOGGER.warning(
            "Skipping Cargo.lock discovery after git ls-files failed: %s", detail
        )
    return None


def _filter_lockfiles_with_manifests(
    workspace_root: Path,
    stdout: str,
) -> tuple[Path, ...]:
    """Return lockfile paths from ``stdout`` that have an adjacent ``Cargo.toml``."""
    lockfiles: list[Path] = []
    for line in stdout.splitlines():
        relative_path = line.strip()
        if not relative_path:
            continue
        lockfile_path = workspace_root / relative_path
        if (lockfile_path.parent / "Cargo.toml").exists():
            lockfiles.append(lockfile_path)
    return tuple(lockfiles)


def discover_tracked_lockfiles(
    workspace_root: Path,
    runner: _CommandRunner,
) -> tuple[Path, ...]:
    """Return tracked Cargo.lock files with adjacent manifests."""
    has_lockfiles = any(
        "target" not in path.relative_to(workspace_root).parts
        for path in workspace_root.rglob("Cargo.lock")
    )
    if not has_lockfiles:
        return ()
    stdout = _query_git_tracked_lockfiles(workspace_root, runner)
    if stdout is None:
        return ()
    return _filter_lockfiles_with_manifests(workspace_root, stdout)


def refresh_lockfile(
    manifest_path: Path,
    runner: _CommandRunner,

    """Regenerate the lockfile for ``manifest_path`` and return its path."""
    exit_code, stdout, stderr = runner(
        ("cargo", "generate-lockfile", "--manifest-path", str(manifest_path)),
        cwd=manifest_path.parent,
    )
    if exit_code != 0:
        detail = (stderr or stdout).strip()
        message = f"Failed to refresh {manifest_path.parent / 'Cargo.lock'}"
        if detail:
            message = f"{message}: {detail}"
        raise LockfileRefreshError(message)
    return manifest_path.parent / "Cargo.lock"


def validate_lockfile_freshness(
    manifest_path: Path,
    runner: _CommandRunner,
) -> bool:
    """Return whether Cargo accepts ``manifest_path`` under ``--locked``."""
    exit_code, _stdout, _stderr = runner(
        (
            "cargo",
            "metadata",
            "--locked",
            "--manifest-path",
            str(manifest_path),
            "--format-version=1",
        ),
        cwd=manifest_path.parent,
    )
    return exit_code == 0
