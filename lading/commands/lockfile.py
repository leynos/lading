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
import time
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError
from lading.utils import metrics
from lading.utils.process import append_detail, command_detail, with_detail

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

LOGGER = logging.getLogger(__name__)
_ManifestExists = cabc.Callable[[Path], bool]

# Metric names (issue #91); documented in docs/developers-guide.md.
DISCOVERED_LOCKFILES_METRIC = "lockfile.discovered"
REFRESH_METRIC = "lockfile.refresh"
REFRESH_DURATION_METRIC = "lockfile.refresh.duration"
VALIDATE_METRIC = "lockfile.validate"
VALIDATE_DURATION_METRIC = "lockfile.validate.duration"


class LockfileRefreshError(LadingError):
    """Raised when Cargo cannot regenerate a lockfile."""


class LockfileDiscoveryError(LadingError):
    """Raised when git cannot list tracked lockfiles."""


class NotAGitRepositoryError(LockfileDiscoveryError):
    """Raised when lockfile discovery targets a directory outside git control.

    Callers that treat a non-git workspace as "nothing to validate" should
    catch this subclass and decide the skip policy themselves; discovery no
    longer hides the condition behind a warning and an empty result.
    """


@dc.dataclass(frozen=True, slots=True)
class LockfileFreshness:
    """Result from validating a lockfile under Cargo's locked mode."""

    is_fresh: bool
    is_stale: bool = False
    detail: str = ""


def _raise_git_ls_files_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    workspace_root: Path,
) -> typ.NoReturn:
    """Raise the typed discovery error for a failed ``git ls-files`` call."""
    detail = command_detail(stdout, stderr)
    if "not a git repository" in detail.lower():
        message = f"{workspace_root} is not a git repository"
        raise NotAGitRepositoryError(message)
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
        with an adjacent ``Cargo.toml`` manifest.
        :func:`_lockfiles_with_manifests` applies manifest and path filtering.

    Raises
    ------
    NotAGitRepositoryError
        If ``workspace_root`` is not under git control. Callers own the skip
        policy for that condition.
    LockfileDiscoveryError
        If ``git ls-files`` fails for any other reason.

    Notes
    -----
    Filesystem access is confined to the injected ``manifest_exists`` port;
    git access is confined to the injected ``runner``. The function performs
    no direct I/O of its own, so integration tests can exercise it against
    real directories and unit tests can substitute both ports.
    """
    exit_code, stdout, stderr = runner(
        ("git", "ls-files", "**/Cargo.lock", "Cargo.lock"),
        cwd=workspace_root,
    )
    if exit_code != 0:
        _raise_git_ls_files_failure(exit_code, stdout, stderr, workspace_root)
    lockfiles = _lockfiles_with_manifests(stdout, workspace_root, manifest_exists)
    if lockfiles:
        # Skip a zero-amount increment: it would create a 0-valued counter key
        # and force an otherwise-silent exit summary (quiet runs stay quiet).
        metrics.increment_counter(DISCOVERED_LOCKFILES_METRIC, amount=len(lockfiles))
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
    started_at = time.perf_counter()
    exit_code, stdout, stderr = runner(
        ("cargo", "generate-lockfile", "--manifest-path", str(manifest_path)),
        cwd=manifest_path.parent,
    )
    metrics.observe_duration(REFRESH_DURATION_METRIC, time.perf_counter() - started_at)
    if exit_code != 0:
        metrics.increment_counter(REFRESH_METRIC, outcome="failure")
        message = with_detail(f"Failed to refresh {lockfile_path}", stdout, stderr)
        raise LockfileRefreshError(message)
    metrics.increment_counter(REFRESH_METRIC, outcome="success")
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
    started_at = time.perf_counter()
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
    metrics.observe_duration(VALIDATE_DURATION_METRIC, time.perf_counter() - started_at)
    detail = command_detail(stdout, stderr)
    is_fresh = exit_code == 0
    is_stale = _is_lockfile_stale_detail(detail)
    state = "fresh"
    if not is_fresh:
        state = "stale" if is_stale else "failed"
    metrics.increment_counter(VALIDATE_METRIC, outcome=state)
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
