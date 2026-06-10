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

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError
from lading.utils.process import append_detail, command_detail, with_detail

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

LOGGER = logging.getLogger(__name__)
_ManifestExists = cabc.Callable[[Path], bool]


class LockfileRefreshError(LadingError):
    """Raised when Cargo cannot regenerate a lockfile."""


class LockfileDiscoveryError(LadingError):
    """Raised when git cannot list tracked lockfiles."""


@dc.dataclass(frozen=True, slots=True)
class LockfileFreshness:
    """Result from validating a lockfile under Cargo's locked mode."""

    is_fresh: bool
    is_stale: bool = False
    detail: str = ""


def _handle_git_ls_files_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    workspace_root: Path,
) -> tuple[Path, ...] | None:
    """Return ``None`` for git success, or an empty result for git failure."""
    if exit_code == 0:
        return None
    detail = command_detail(stdout, stderr)
    if "not a git repository" in detail.lower():
        LOGGER.warning(
            "Skipping Cargo.lock discovery because %s is not a git repository",
            workspace_root,
        )
        return ()
    # Unlike the other sites, git may exit non-zero with no output at all, so
    # fall back to the status code before handing the detail to append_detail.
    fallback = f"git ls-files exited with status {exit_code}"
    message = append_detail(
        f"Failed to discover tracked Cargo.lock files in {workspace_root}",
        detail or fallback,
    )
    raise LockfileDiscoveryError(message)


def _lockfiles_with_manifests(
    stdout: str,
    workspace_root: Path,
    manifest_exists: _ManifestExists,
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
        if manifest_exists(lockfile_path.parent / "Cargo.toml"):
            lockfiles.append(lockfile_path)
    return tuple(lockfiles)


def _manifest_exists(manifest_path: Path) -> bool:
    """Return whether ``manifest_path`` exists on disk."""
    return manifest_path.exists()


def discover_tracked_lockfiles(
    workspace_root: Path,
    runner: CommandRunner,
    *,
    manifest_exists: _ManifestExists = _manifest_exists,
) -> tuple[Path, ...]:
    """Return tracked Cargo.lock files with adjacent manifests.

    Parameters
    ----------
    workspace_root
        Path to the repository root that should be searched for lockfiles.
    runner
        Callable used to execute shell commands. It receives a command
        sequence and returns ``(exit_code, stdout, stderr)``.
    manifest_exists
        Callable used to decide whether a candidate lockfile has an adjacent
        manifest. The default adapter checks the filesystem.

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
        ("git", "ls-files", "**/Cargo.lock", "Cargo.lock"),
        cwd=workspace_root,
    )
    error_result = _handle_git_ls_files_failure(
        exit_code, stdout, stderr, workspace_root
    )
    if error_result is not None:
        return error_result
    lockfiles = _lockfiles_with_manifests(stdout, workspace_root, manifest_exists)
    LOGGER.info(
        "Discovered %d tracked lockfile(s) with adjacent manifests in %s",
        len(lockfiles),
        workspace_root,
    )
    return lockfiles


def refresh_lockfile(
    manifest_path: Path,
    runner: CommandRunner,
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
    lockfile_path = manifest_path.parent / "Cargo.lock"
    LOGGER.info("Refreshing %s", lockfile_path)
    exit_code, stdout, stderr = runner(
        ("cargo", "generate-lockfile", "--manifest-path", str(manifest_path)),
        cwd=manifest_path.parent,
    )
    if exit_code != 0:
        message = with_detail(f"Failed to refresh {lockfile_path}", stdout, stderr)
        raise LockfileRefreshError(message)
    LOGGER.info("Refreshed %s", lockfile_path)
    return lockfile_path


def validate_lockfile_freshness(
    manifest_path: Path,
    runner: CommandRunner,
) -> LockfileFreshness:
    """Return Cargo's locked-mode freshness result for ``manifest_path``.

    Parameters
    ----------
    manifest_path
        Path to the Cargo manifest file to validate.
    runner
        Callable used to execute the cargo command. It receives a command
        sequence and returns ``(exit_code, stdout, stderr)``.

    Returns
    -------
    LockfileFreshness
        Structured result describing whether the lockfile is fresh, stale
        because Cargo says it needs updating under ``--locked``, or failed for
        another reason.
    """
    exit_code, stdout, stderr = runner(
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
    detail = command_detail(stdout, stderr)
    is_fresh = exit_code == 0
    is_stale = _is_lockfile_stale_detail(detail)
    state = "fresh"
    if not is_fresh:
        state = "stale" if is_stale else "failed"
    LOGGER.info(
        "Validated lockfile freshness for %s: %s",
        manifest_path,
        state,
    )
    return LockfileFreshness(is_fresh=is_fresh, is_stale=is_stale, detail=detail)


def _is_lockfile_stale_detail(detail: str) -> bool:
    """Return whether Cargo reported a locked lockfile needing regeneration."""
    normalized = detail.lower()
    return "--locked" in normalized and (
        "needs to be updated" in normalized
        or "cannot update the lock file" in normalized
    )
