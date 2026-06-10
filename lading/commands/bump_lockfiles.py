"""Lockfile regeneration helpers for ``lading bump``.

The public entry point is :func:`regenerate_lockfiles`, which rebuilds the
workspace root lockfile and any configured nested lockfiles after manifest
versions change.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import shlex
import time
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError
from lading.runtime import CommandRunner, subprocess_runner
from lading.utils.process import with_detail

_LOGGER = logging.getLogger(__name__)


class LockfileRegenerationError(LadingError):
    """Raise when lockfile regeneration cannot validate or execute."""


def resolve_lockfile_paths(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
) -> tuple[Path, ...]:
    """Return lockfile paths implied by configured manifest paths."""
    manifests = _resolve_manifest_paths(workspace_root, lockfile_manifests)
    return tuple(manifest.parent / "Cargo.lock" for manifest in manifests)


def regenerate_lockfiles(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
    *,
    runner: CommandRunner | None = None,
) -> tuple[Path, ...]:
    """Regenerate Cargo lockfiles for root and configured manifests.

    Parameters
    ----------
    workspace_root : Path
        Absolute path to the Cargo workspace root.
    lockfile_manifests : Sequence[str]
        Configured manifest paths relative to *workspace_root*. The workspace
        root ``Cargo.toml`` is always prepended and de-duplicated.
    runner : CommandRunner or None, optional
        Callable used to invoke ``cargo``. Defaults to
        :func:`lading.runtime.subprocess_runner` when ``None``.

    Returns
    -------
    tuple[Path, ...]
        Paths to every ``Cargo.lock`` file that was regenerated, in manifest
        execution order.

    Raises
    ------
    LockfileRegenerationError
        If any configured manifest path is invalid (outside the workspace or
        not named ``Cargo.toml``), or — after every manifest has been
        attempted — if ``cargo update --workspace`` failed. When only the
        workspace-root lockfile is regenerated, the original cargo error is
        re-raised unchanged. When several lockfiles are regenerated, one
        aggregated error is raised whose message lists each failed manifest
        with a repair command.

    Notes
    -----
    **Partial-update semantics:** regeneration is not atomic and successful
    updates are not rolled back when a later manifest fails. Every manifest
    is attempted (issue #84), so a single cargo failure does not leave
    unrelated lockfiles silently stale. When several lockfiles are
    regenerated, the aggregated error tells the operator exactly which
    lockfiles still need repair and how; a lone root-lockfile failure needs no
    such disambiguation and surfaces the plain cargo error.
    """
    command_runner = subprocess_runner if runner is None else runner
    manifests = _resolve_manifest_paths(workspace_root, lockfile_manifests)
    started_at = time.perf_counter()
    _LOGGER.info("Regenerating %d Cargo lockfile(s)", len(manifests))
    lockfiles, failures = _collect_lockfile_results(
        manifests, workspace_root, command_runner
    )
    if failures:
        _raise_aggregated_failure(manifests, failures)
    _LOGGER.info(
        "Regenerated %d Cargo lockfile(s) in %.3fs",
        len(lockfiles),
        time.perf_counter() - started_at,
    )
    return tuple(lockfiles)


def _collect_lockfile_results(
    manifests: tuple[Path, ...],
    workspace_root: Path,
    command_runner: CommandRunner,
) -> tuple[list[Path], list[tuple[Path, LockfileRegenerationError]]]:
    """Attempt every manifest and partition the outcomes into a pair."""
    lockfiles: list[Path] = []
    failures: list[tuple[Path, LockfileRegenerationError]] = []
    for manifest in manifests:
        result = _attempt_single_lockfile_update(
            workspace_root, manifest, command_runner
        )
        if result.error is not None:
            failures.append((manifest, result.error))
        elif result.lockfile is not None:
            lockfiles.append(result.lockfile)
    return lockfiles, failures


def _raise_aggregated_failure(
    manifests: tuple[Path, ...],
    failures: cabc.Sequence[tuple[Path, LockfileRegenerationError]],
) -> typ.NoReturn:
    """Raise the appropriate error for one or more failed manifests."""
    if len(manifests) == 1:
        raise failures[0][1]
    cause = failures[0][1].__cause__ or failures[0][1]
    raise LockfileRegenerationError(
        _build_aggregate_failure_message(failures)
    ) from cause


def _build_aggregate_failure_message(
    failures: cabc.Sequence[tuple[Path, LockfileRegenerationError]],
) -> str:
    """Return the aggregated operator-facing regeneration failure message."""
    header = (
        f"Cargo lockfile regeneration failed for {len(failures)} manifest(s). "
        "Manifests already carry the new version, so the workspace is "
        "inconsistent until each lockfile below is repaired:"
    )
    lines = [header]
    for manifest, error in failures:
        lines.append(f"- {error}")
        quoted_manifest = shlex.quote(str(manifest))
        lines.append(f"  cargo update --workspace --manifest-path {quoted_manifest}")
    return "\n".join(lines)


def _resolve_manifest_paths(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
) -> tuple[Path, ...]:
    """Return validated root and configured manifest paths in execution order."""
    resolved_root = workspace_root.resolve()
    root_manifest = (workspace_root / "Cargo.toml").resolve()
    seen_manifests: set[Path] = {root_manifest}
    manifests = [root_manifest]
    for manifest in lockfile_manifests:
        candidate = (workspace_root / manifest).resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError as exc:
            message = (
                f"Lockfile manifest path must stay within the workspace: {manifest}"
            )
            raise LockfileRegenerationError(message) from exc
        if candidate.name != "Cargo.toml":
            message = (
                f"Lockfile manifest path must point to a Cargo.toml file: {manifest}"
            )
            raise LockfileRegenerationError(message)
        if candidate in seen_manifests:
            continue
        seen_manifests.add(candidate)
        manifests.append(candidate)
    return tuple(manifests)


@dc.dataclass(frozen=True, slots=True)
class _LockfileUpdateResult:
    """Outcome of attempting one manifest's lockfile update."""

    lockfile: Path | None
    error: LockfileRegenerationError | None


def _attempt_single_lockfile_update(
    workspace_root: Path,
    manifest: Path,
    command_runner: CommandRunner,
) -> _LockfileUpdateResult:
    """Attempt one manifest's lockfile update and return the outcome."""
    manifest_started_at = time.perf_counter()
    _LOGGER.info("Regenerating Cargo lockfile for %s", manifest)
    try:
        _run_workspace_lockfile_update(workspace_root, manifest, command_runner)
    except LockfileRegenerationError as exc:
        # Keep going: attempting the remaining manifests gives the
        # operator one aggregated repair list instead of a re-run per
        # failure (issue #84).
        _LOGGER.exception("Cargo lockfile regeneration failed")
        return _LockfileUpdateResult(lockfile=None, error=exc)
    _LOGGER.info(
        "Regenerated Cargo lockfile for %s in %.3fs",
        manifest,
        time.perf_counter() - manifest_started_at,
    )
    return _LockfileUpdateResult(lockfile=manifest.parent / "Cargo.lock", error=None)


def _run_workspace_lockfile_update(
    workspace_root: Path,
    manifest_path: Path,
    runner: CommandRunner,
) -> None:
    """Invoke cargo for one manifest and surface failures consistently."""
    try:
        exit_code, stdout, stderr = runner(
            (
                "cargo",
                "update",
                "--workspace",
                "--manifest-path",
                str(manifest_path),
            ),
            cwd=workspace_root,
        )
    except Exception as exc:
        message = f"Cargo lockfile regeneration failed for {manifest_path}: {exc}"
        raise LockfileRegenerationError(message) from exc
    if exit_code != 0:
        message = with_detail(
            "Cargo lockfile regeneration failed for "
            f"{manifest_path} with exit code {exit_code}",
            stdout,
            stderr,
        )
        raise LockfileRegenerationError(message)


@dc.dataclass(frozen=True, slots=True)
class CargoLockfileRepository:
    """Cargo-backed :class:`LockfileRepository` bound to a command runner."""

    runner: CommandRunner | None = None

    def resolve_lockfile_paths(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Return the lockfile paths a regeneration run would touch."""
        return resolve_lockfile_paths(workspace_root, lockfile_manifests)

    def regenerate_lockfiles(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Regenerate lockfiles via ``cargo update --workspace``."""
        return regenerate_lockfiles(
            workspace_root, lockfile_manifests, runner=self.runner
        )


class LockfileRepository(typ.Protocol):
    """Port for projecting and regenerating Cargo lockfiles after a bump.

    The bump domain depends on this protocol rather than on a command
    runner, keeping execution infrastructure out of the public bump options
    (issue #82).
    """

    def resolve_lockfile_paths(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Return the lockfile paths a regeneration run would touch."""

    def regenerate_lockfiles(
        self,
        workspace_root: Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[Path, ...]:
        """Regenerate lockfiles and return the rewritten paths."""
