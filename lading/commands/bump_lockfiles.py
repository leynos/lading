"""Lockfile regeneration helpers for ``lading bump``.

The public entry point is :func:`regenerate_lockfiles`, which rebuilds the
workspace root lockfile and any configured nested lockfiles after manifest
versions change.
"""

from __future__ import annotations

import collections.abc as cabc
import logging
import time
from pathlib import Path

from lading.runtime import CommandRunner, subprocess_runner

_LOGGER = logging.getLogger(__name__)


class LockfileRegenerationError(RuntimeError):
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
    """Regenerate Cargo lockfiles for root and configured manifests."""
    command_runner = subprocess_runner if runner is None else runner
    manifests = _resolve_manifest_paths(workspace_root, lockfile_manifests)
    started_at = time.perf_counter()
    _LOGGER.info("Regenerating %d Cargo lockfile(s)", len(manifests))
    lockfiles: list[Path] = []
    for manifest in manifests:
        manifest_started_at = time.perf_counter()
        _LOGGER.info("Regenerating Cargo lockfile for %s", manifest)
        _run_workspace_lockfile_update(workspace_root, manifest, command_runner)
        _LOGGER.info(
            "Regenerated Cargo lockfile for %s in %.3fs",
            manifest,
            time.perf_counter() - manifest_started_at,
        )
        lockfiles.append(manifest.parent / "Cargo.lock")
    _LOGGER.info(
        "Regenerated %d Cargo lockfile(s) in %.3fs",
        len(lockfiles),
        time.perf_counter() - started_at,
    )
    return tuple(lockfiles)


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
        message = (
            "Cargo lockfile regeneration failed for "
            f"{manifest_path} with exit code {exit_code}"
        )
        if detail := (stderr or stdout).strip():
            message = f"{message}: {detail}"
        raise LockfileRegenerationError(message)
