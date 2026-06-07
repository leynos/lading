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


def _git_ls_files_error_result(
    exit_code: int,
    stdout: str,
    stderr: str,
    workspace_root: Path,
) -> tuple[Path, ...] | None:
    """Return an empty result for failed ``git ls-files`` invocations."""
    if exit_code == 0:
        return None
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
    return ()


def _lockfiles_with_manifests(
    stdout: str,
    workspace_root: Path,
) -> tuple[Path, ...]:
    """Return lockfile paths from ``git ls-files`` with adjacent manifests."""
    lockfiles: list[Path] = []
    for line in stdout.splitlines():
        relative_path = line.strip()
        if not relative_path:
            continue
        lockfile_path = workspace_root / relative_path
        if "target" in lockfile_path.relative_to(workspace_root).parts:
            continue
        if (lockfile_path.parent / "Cargo.toml").exists():
            lockfiles.append(lockfile_path)
    return tuple(lockfiles)


def discover_tracked_lockfiles(
    workspace_root: Path,
    runner: _CommandRunner,
) -> tuple[Path, ...]:
    """Return tracked Cargo.lock files with adjacent manifests."""
    candidate_lockfiles = tuple(
        path
        for path in workspace_root.rglob("Cargo.lock")
        if "target" not in path.relative_to(workspace_root).parts
    )
    if not candidate_lockfiles:
        return ()
    exit_code, stdout, stderr = runner(
        ("git", "ls-files", "*/Cargo.lock", "Cargo.lock"),
        cwd=workspace_root,
    )
    error_result = _git_ls_files_error_result(exit_code, stdout, stderr, workspace_root)
    if error_result is not None:
        return error_result
    return _lockfiles_with_manifests(stdout, workspace_root)


def refresh_lockfile(
    manifest_path: Path,
    runner: _CommandRunner,
) -> Path:
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
