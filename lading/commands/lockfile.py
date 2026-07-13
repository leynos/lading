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
``lading bump`` regenerates lockfiles via
:func:`lading.commands.bump_lockfiles.regenerate_lockfiles` instead, which
runs ``cargo update --workspace``: bump wants existing pinned versions
refreshed in place after manifest rewrites, whereas validation here uses
``cargo metadata --locked`` purely as a read-only freshness probe.

The publish pre-flight domain reaches these operations through the
:class:`LockfileInspectionRepository` port (issue #82) rather than holding a
raw command runner. :class:`CargoLockfileInspectionRepository` is the
git- and cargo-backed adapter, bound to a runner (and optional environment
overrides) at the pre-flight composition root.

Typical publish-side usage:

```python
repository = CargoLockfileInspectionRepository(runner=runner)
lockfiles = repository.discover_tracked_lockfiles(workspace_root)
for lockfile_path in lockfiles:
    repository.validate_lockfile_freshness(lockfile_path.parent / "Cargo.toml")
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
_ManifestExists = cabc.Callable[[Path], bool]

# Metric names (issue #91); documented in docs/developers-guide.md.
DISCOVERED_LOCKFILES_METRIC = "lockfile.discovered"
VALIDATE_METRIC = "lockfile.validate"
VALIDATE_DURATION_METRIC = "lockfile.validate.duration"


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


@dc.dataclass(frozen=True, slots=True)
class CargoLockfileInspectionRepository:
    """Git- and cargo-backed adapter for publish-side lockfile inspection.

    Binds a :class:`~lading.runtime.CommandRunner` (and optional environment
    overrides) so the publish pre-flight domain can discover tracked lockfiles
    and probe their freshness without holding a raw command runner (issue #82).
    The adapter applies ``env`` to any invocation that does not supply its own,
    matching the behaviour the pre-flight base environment previously wired in
    through an inline runner wrapper.

    Attributes
    ----------
    runner : CommandRunner
        Command runner used to execute the git discovery and cargo freshness
        probes.
    env : Mapping[str, str] | None, default None
        Environment overrides applied to any invocation that does not supply
        its own; ``None`` leaves each call's environment untouched.
    manifest_exists : Callable[[Path], bool], default _manifest_exists
        Predicate deciding whether a discovered lockfile has an adjacent
        ``Cargo.toml`` manifest; the default checks the filesystem.
    """

    runner: CommandRunner
    env: cabc.Mapping[str, str] | None = None
    manifest_exists: _ManifestExists = _manifest_exists

    def discover_tracked_lockfiles(self, workspace_root: Path) -> tuple[Path, ...]:
        """Return tracked Cargo.lock files with adjacent manifests.

        Parameters
        ----------
        workspace_root
            Path to the repository root searched for tracked lockfiles.

        Returns
        -------
        tuple[Path, ...]
            Git-tracked ``Cargo.lock`` files outside any ``target`` directory
            that have an adjacent ``Cargo.toml`` manifest.

        """
        return discover_tracked_lockfiles(
            workspace_root,
            self._bound_runner(),
            manifest_exists=self.manifest_exists,
        )

    def validate_lockfile_freshness(self, manifest_path: Path) -> LockfileFreshness:
        """Return Cargo's locked-mode freshness result for ``manifest_path``.

        Parameters
        ----------
        manifest_path
            Path to the Cargo manifest to validate under ``--locked``.

        Returns
        -------
        LockfileFreshness
            Structured result describing whether the lockfile is fresh, stale
            (Cargo says it needs updating under ``--locked``), or failed for
            another reason.

        """
        return validate_lockfile_freshness(manifest_path, self._bound_runner())

    def _bound_runner(self) -> CommandRunner:
        """Return ``runner`` with ``env`` applied when a call omits its own."""
        if self.env is None:
            return self.runner
        base_env = self.env
        base_runner = self.runner

        def runner_with_env(
            command: cabc.Sequence[str],
            *,
            cwd: Path | None = None,
            env: cabc.Mapping[str, str] | None = None,
            **runner_kwargs: bool,
        ) -> tuple[int, str, str]:
            """Invoke ``base_runner`` with ``base_env`` as the default env.

            Any extra keyword (notably ``echo_stdout``) is forwarded to
            ``base_runner`` unchanged; only ``env`` is defaulted (to the bound
            ``base_env``) when a call omits it.
            """
            effective_env = base_env if env is None else env
            return base_runner(command, cwd=cwd, env=effective_env, **runner_kwargs)

        return runner_with_env


class LockfileInspectionRepository(typ.Protocol):
    """Port for discovering tracked lockfiles and probing their freshness.

    The publish pre-flight domain depends on this protocol rather than on a
    command runner, keeping VCS, filesystem, and cargo execution concerns out
    of the freshness-classification logic (issue #82). This is the publish-side
    counterpart to :class:`lading.commands.bump_lockfiles.LockfileRepository`,
    which owns bump-side lockfile projection and regeneration.
    """

    def discover_tracked_lockfiles(self, workspace_root: Path) -> tuple[Path, ...]:
        """Return tracked Cargo.lock files with adjacent manifests.

        Parameters
        ----------
        workspace_root
            Path to the repository root searched for tracked lockfiles.

        Returns
        -------
        tuple[Path, ...]
            Git-tracked ``Cargo.lock`` files outside any ``target`` directory
            that have an adjacent ``Cargo.toml`` manifest.

        """

    def validate_lockfile_freshness(self, manifest_path: Path) -> LockfileFreshness:
        """Return the freshness result for ``manifest_path``.

        Parameters
        ----------
        manifest_path
            Path to the Cargo manifest to validate.

        Returns
        -------
        LockfileFreshness
            The freshness result, distinguishing fresh, stale, and failed
            states.

        """
