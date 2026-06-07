"""Cargo lockfile discovery, refresh, and freshness validation helpers.

This module centralises the Cargo lockfile operations shared by release
workflows. It discovers lockfiles that belong to the source workspace,
regenerates them after manifest rewrites, and validates that Cargo can read
them under ``--locked`` before expensive publish pre-flight commands run.

Discovery is intentionally conservative. :func:`discover_tracked_lockfiles`
queries the git index for tracked ``Cargo.lock`` files, then narrows the
result to paths that are not under a ``target`` directory and have an adjacent
``Cargo.toml`` manifest.

``lading bump`` calls :func:`discover_tracked_lockfiles` followed by
:func:`refresh_lockfile` after it updates manifest versions. ``lading publish``
uses :func:`discover_tracked_lockfiles` and
:func:`validate_lockfile_freshness` before the cargo check/test pre-flight, so
stale lockfiles fail early with an actionable repair command.

Typical local or CI usage follows the same sequence:

```python
lockfiles = discover_tracked_lockfiles(workspace_root, runner)
for lockfile_path in lockfiles:
    refresh_lockfile(lockfile_path.parent / "Cargo.toml", runner)
    validate_lockfile_freshness(lockfile_path.parent / "Cargo.toml", runner)
```
"""

from __future__ import annotations

import logging
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError

if typ.TYPE_CHECKING:
    from lading.commands.publish_execution import _CommandRunner

LOGGER = logging.getLogger(__name__)


class LockfileRefreshError(LadingError):
    """Raised when Cargo cannot regenerate a lockfile."""


def _handle_git_ls_files_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    workspace_root: Path,
) -> tuple[Path, ...] | None:
    """Return ``None`` for git success, or an empty result for git failure."""
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
    """Return tracked Cargo.lock files with adjacent manifests.

    Parameters
    ----------
    workspace_root
        Path to the repository root that should be searched for lockfiles.
    runner
        Callable used to execute shell commands. It receives a command
        sequence and returns ``(exit_code, stdout, stderr)``.

    Returns
    -------
    tuple[Path, ...]
        Git-tracked ``Cargo.lock`` files outside any ``target`` directory and
        with an adjacent ``Cargo.toml`` manifest. The helper
        :func:`_handle_git_ls_files_failure` handles git failures, and
        :func:`_lockfiles_with_manifests` applies manifest and path filtering.

    Notes
    -----
    If ``workspace_root`` is not a git repository, discovery logs a warning
    through :func:`_handle_git_ls_files_failure` and returns an empty tuple.
    """
    exit_code, stdout, stderr = runner(
        ("git", "ls-files", "*/Cargo.lock", "Cargo.lock"),
        cwd=workspace_root,
    )
    error_result = _handle_git_ls_files_failure(
        exit_code, stdout, stderr, workspace_root
    )
    if error_result is not None:
        return error_result
    return _lockfiles_with_manifests(stdout, workspace_root)


def refresh_lockfile(
    manifest_path: Path,
    runner: _CommandRunner,
) -> Path:
    """Regenerate the lockfile for ``manifest_path`` and return its path.

    Parameters
    ----------
    manifest_path
        Path to the ``Cargo.toml`` manifest to generate a lockfile for.
    runner
        Callable used to run external commands. It receives a command sequence
        and returns ``(exit_code, stdout, stderr)``.

    Returns
    -------
    Path
        Path to the generated ``Cargo.lock`` in ``manifest_path.parent``.

    Raises
    ------
    LockfileRefreshError
        Raised when ``cargo generate-lockfile`` returns a non-zero exit code.
        The error message includes Cargo's stderr, or stdout when stderr is
        empty, so callers can report why regeneration failed.
    """
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
    LOGGER.info("Refreshed %s", manifest_path.parent / "Cargo.lock")
    return manifest_path.parent / "Cargo.lock"


def validate_lockfile_freshness(
    manifest_path: Path,
    runner: _CommandRunner,
) -> bool:
    """Return whether Cargo accepts ``manifest_path`` under ``--locked``.

    Parameters
    ----------
    manifest_path
        Path to the Cargo manifest file to validate.
    runner
        Callable used to execute the cargo command. It receives a command
        sequence and returns ``(exit_code, stdout, stderr)``.

    Returns
    -------
    bool
        ``True`` if ``cargo metadata --locked`` succeeds with exit code 0;
        ``False`` otherwise.
    """
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
