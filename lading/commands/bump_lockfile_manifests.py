"""Manifest discovery and merging for Cargo lockfile regeneration."""

from __future__ import annotations

import collections.abc as cabc
from pathlib import Path

from lading.commands.lockfile import discover_tracked_lockfiles
from lading.runtime import CommandRunner, subprocess_runner


def merge_discovered_manifests(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
    *,
    runner: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Return configured manifests plus discovered tracked-lockfile manifests.

    Parameters
    ----------
    workspace_root : Path
        Absolute path to the Cargo workspace root.
    lockfile_manifests : Sequence[str]
        Configured manifest paths relative to *workspace_root*.
    runner : CommandRunner or None, optional
        Callable used to invoke ``git``. Defaults to
        :func:`lading.runtime.subprocess_runner` when ``None``.

    Returns
    -------
    tuple[str, ...]
        The configured entries in their original order, followed by
        workspace-relative POSIX manifest paths implied by git-tracked
        ``Cargo.lock`` files (via
        :func:`lading.commands.lockfile.discover_tracked_lockfiles`) in
        sorted order, skipping any manifest already configured under an
        equivalent spelling. In a non-git workspace the configured tuple is
        returned unchanged.

    Examples
    --------
    With ``fixtures/minimal/Cargo.lock`` tracked in git:

    ```python
    merge_discovered_manifests(workspace_root, ("crates/ui/Cargo.toml",))
    # ("crates/ui/Cargo.toml", "Cargo.toml", "fixtures/minimal/Cargo.toml")
    ```
    """
    command_runner = subprocess_runner if runner is None else runner
    discovered = discover_tracked_lockfiles(workspace_root, command_runner)
    seen_manifests = {
        (workspace_root / manifest).resolve() for manifest in lockfile_manifests
    }
    merged = list(lockfile_manifests)
    discovered_relative = sorted(
        (lockfile_path.parent / "Cargo.toml").relative_to(workspace_root).as_posix()
        for lockfile_path in discovered
    )
    for relative_manifest in discovered_relative:
        resolved = (workspace_root / relative_manifest).resolve()
        if resolved in seen_manifests:
            continue
        seen_manifests.add(resolved)
        merged.append(relative_manifest)
    return tuple(merged)
