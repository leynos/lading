"""Cargo lockfile discovery and freshness validation helpers.

This module centralises the Cargo lockfile operations shared by release
workflows. It discovers lockfiles that belong to the source workspace and
validates that Cargo can read them under ``--locked`` before expensive
publish pre-flight commands run.

Discovery is intentionally conservative. :func:`discover_tracked_lockfiles`
queries the git index for tracked ``Cargo.lock`` files, then narrows the
result to paths that are not under a ``target`` directory and have an adjacent
``Cargo.toml`` manifest.

Call graph: ``lading publish`` uses :func:`discover_tracked_lockfiles` and
:func:`validate_lockfile_freshness` before the cargo check/test pre-flight,
so stale lockfiles fail early with an actionable repair command.
``lading bump`` uses :func:`discover_tracked_lockfiles` too — via
:func:`lading.commands.bump_lockfiles.merge_discovered_manifests` — but then
regenerates the workspace-root lockfile from ``Cargo.toml``, together with the
discovered and configured lockfiles, through
:func:`lading.commands.bump_lockfiles.regenerate_lockfiles`, which runs
``cargo update --workspace``: bump wants existing pinned versions refreshed
in place after manifest rewrites, whereas validation here uses
``cargo metadata --locked`` purely as a read-only freshness probe.

The publish pre-flight domain reaches these operations through the
:class:`~lading.commands.lockfile_repository.LockfileInspectionRepository` port
(issue #82) rather than holding a raw command runner. That port and its
git- and cargo-backed adapter live in
:mod:`lading.commands.lockfile_repository`, leaving this module to the domain
operations themselves.

Typical direct usage:

```python
from pathlib import Path

lockfiles = discover_tracked_lockfiles(Path("."), runner)
for lockfile_path in lockfiles:
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
from lading.utils.process import append_detail, command_detail

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

LOGGER = logging.getLogger(__name__)
type _ManifestExists = cabc.Callable[[Path], bool]

# Metric names (issue #91); documented in docs/developers-guide.md.
DISCOVERED_LOCKFILES_METRIC = "lockfile.discovered"
DISCOVERY_FAILURE_METRIC = "lockfile.discovery.failed"
VALIDATE_METRIC = "lockfile.validate"
VALIDATE_DURATION_METRIC = "lockfile.validate.duration"


class LockfileDiscoveryError(LadingError):
    """Raised when git cannot list tracked lockfiles."""


class NotAGitRepositoryError(LockfileDiscoveryError):
    """Raised when lockfile discovery targets a directory outside git control.

    Callers that treat a non-git workspace as "nothing to validate" should
    catch this subclass and decide the skip policy themselves; discovery no
    longer hides the condition behind a warning and an empty result.

    Attributes
    ----------
    workspace_root : Path
        The directory that was found to be outside git control, kept as a
        structured attribute so callers need not parse the message.
    """

    def __init__(self, workspace_root: Path) -> None:
        """Capture the workspace root that is not under git control."""
        self.workspace_root = workspace_root
        super().__init__(f"{workspace_root} is not a git repository")


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
    """Raise the typed discovery error for a failed ``git ls-files`` call.

    Both exits are counted under :data:`DISCOVERY_FAILURE_METRIC` regardless of
    ``emit_observability``, which scopes to success telemetry only. Matching the
    English text is sound because discovery pins the C locale (issue #79);
    ``git ls-files`` exits 128 for every fatal condition, so the status code
    alone cannot separate a non-repository from an unrelated failure.
    """
    detail = command_detail(stdout, stderr)
    if "not a git repository" in detail.lower():
        metrics.increment_counter(DISCOVERY_FAILURE_METRIC, reason="not_git")
        raise NotAGitRepositoryError(workspace_root)
    # Unlike the other sites, git may exit non-zero with no output at all, so
    # fall back to the status code before handing the detail to append_detail.
    fallback = f"git ls-files exited with status {exit_code}"
    message = append_detail(
        f"Failed to discover tracked Cargo.lock files in {workspace_root}",
        detail or fallback,
    )
    metrics.increment_counter(DISCOVERY_FAILURE_METRIC, reason="git_error")
    LOGGER.error(message)
    raise LockfileDiscoveryError(message)


def _lockfiles_with_manifests(
    stdout: str,
    workspace_root: Path,
    manifest_exists: _ManifestExists,
) -> tuple[Path, ...]:
    """Return tracked lockfiles outside ``target`` with adjacent manifests."""
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


def discover_tracked_lockfiles(
    workspace_root: Path,
    runner: CommandRunner,
    *,
    manifest_exists: _ManifestExists = Path.exists,
    emit_observability: bool = True,
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
    emit_observability : bool, optional
        Whether successful discovery records metrics and an informational log.
        It suppresses success telemetry only: the error paths below still
        raise when false, so a quiet caller cannot mistake a failed discovery
        for an empty one.

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
    real directories and unit tests can substitute both ports. Classification of
    the failure below matches git's English text, so every production adapter
    pins the C locale on the runner it injects (see
    :func:`lading.utils.process.c_locale_env`).
    """
    exit_code, stdout, stderr = runner(
        ("git", "ls-files", "**/Cargo.lock", "Cargo.lock"),
        cwd=workspace_root,
    )
    if exit_code != 0:
        _raise_git_ls_files_failure(exit_code, stdout, stderr, workspace_root)
    lockfiles = _lockfiles_with_manifests(stdout, workspace_root, manifest_exists)
    if emit_observability and lockfiles:
        # Skip a zero-amount increment: it would create a 0-valued counter key
        # and force an otherwise-silent exit summary (quiet runs stay quiet).
        metrics.increment_counter(DISCOVERED_LOCKFILES_METRIC, amount=len(lockfiles))
    if emit_observability:
        LOGGER.info(
            "Discovered %d tracked lockfile(s) with adjacent manifests in %s",
            len(lockfiles),
            workspace_root,
        )
    return lockfiles


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
