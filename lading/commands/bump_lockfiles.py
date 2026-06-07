"""Lockfile regeneration helpers for ``lading bump``.

The public entry point is :func:`regenerate_lockfiles`, which rebuilds the
workspace root lockfile and any configured nested lockfiles after manifest
versions change.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

from lading.commands.publish_errors import PublishPreflightError
from lading.commands.publish_execution import _invoke

if typ.TYPE_CHECKING:

    class CommandRunner(typ.Protocol):
        """Callable protocol for command execution helpers."""

        def __call__(
            self,
            command: cabc.Sequence[str],
            *,
            cwd: Path | None = None,
        ) -> tuple[int, str, str]:
            """Run ``command`` and return exit code, stdout, and stderr."""

else:
    CommandRunner = cabc.Callable


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
    dry_run: bool,
    runner: CommandRunner | None = None,
) -> tuple[Path, ...]:
    """Regenerate Cargo lockfiles for root and configured manifests."""
    if dry_run:
        return ()

    command_runner = _invoke if runner is None else runner
    manifests = _resolve_manifest_paths(workspace_root, lockfile_manifests)
    lockfiles: list[Path] = []
    for manifest in manifests:
        _run_generate_lockfile(workspace_root, manifest, command_runner)
        lockfiles.append(manifest.parent / "Cargo.lock")
    return tuple(lockfiles)


def _resolve_manifest_paths(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
) -> tuple[Path, ...]:
    """Return root and configured manifest paths in execution order."""
    root_manifest = workspace_root / "Cargo.toml"
    nested_manifests = tuple(
        workspace_root / manifest for manifest in lockfile_manifests
    )
    return (root_manifest, *nested_manifests)


def _run_generate_lockfile(
    workspace_root: Path,
    manifest_path: Path,
    runner: CommandRunner,
) -> None:
    """Invoke cargo for one manifest and surface failures consistently."""
    exit_code, stdout, stderr = runner(
        ("cargo", "generate-lockfile", "--manifest-path", str(manifest_path)),
        cwd=workspace_root,
    )
    if exit_code != 0:
        message = (
            "Cargo lockfile regeneration failed for "
            f"{manifest_path} with exit code {exit_code}"
        )
        if detail := (stderr or stdout).strip():
            message = f"{message}: {detail}"
        raise PublishPreflightError(message)
