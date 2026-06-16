"""Lockfile regeneration helpers for ``lading bump``.

The public entry point is :func:`regenerate_lockfiles`, which rebuilds the
workspace root lockfile and any configured nested lockfiles after manifest
versions change.
"""

from __future__ import annotations

import collections.abc as cabc
import logging
import shlex
import time
from pathlib import Path

from lading.exceptions import LadingError
from lading.runtime import CommandRunner, subprocess_runner

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
        attempted — if ``cargo update --workspace`` failed for one or more
        manifests. The aggregated message lists each failed manifest with a
        repair command.

    Notes
    -----
    **Partial-update semantics:** regeneration is not atomic and successful
    updates are not rolled back when a later manifest fails. Every manifest
    is attempted (issue #84), so a single cargo failure does not leave
    unrelated lockfiles silently stale; the aggregated error tells the
    operator exactly which lockfiles still need repair and how.
    """
    command_runner = subprocess_runner if runner is None else runner
    manifests = _resolve_manifest_paths(workspace_root, lockfile_manifests)
    started_at = time.perf_counter()
    _LOGGER.info("Regenerating %d Cargo lockfile(s)", len(manifests))
    lockfiles, failures = _collect_lockfile_results(
        workspace_root, manifests, command_runner
    )
    _raise_if_failures(failures)
    _LOGGER.info(
        "Regenerated %d Cargo lockfile(s) in %.3fs",
        len(lockfiles),
        time.perf_counter() - started_at,
    )
    return tuple(lockfiles)


def _collect_lockfile_results(
    workspace_root: Path,
    manifests: cabc.Sequence[Path],
    runner: CommandRunner,
) -> tuple[list[Path], list[tuple[Path, LockfileRegenerationError]]]:
    """Attempt every manifest and return successful lockfiles and failures."""
    lockfiles: list[Path] = []
    failures: list[tuple[Path, LockfileRegenerationError]] = []
    for manifest in manifests:
        manifest_started_at = time.perf_counter()
        _LOGGER.info("Regenerating Cargo lockfile for %s", manifest)
        try:
            _run_workspace_lockfile_update(workspace_root, manifest, runner)
        except LockfileRegenerationError as exc:
            # Keep going: attempting the remaining manifests gives the
            # operator one aggregated repair list instead of a re-run per
            # failure (issue #84).
            _LOGGER.exception("Cargo lockfile regeneration failed")
            failures.append((manifest, exc))
            continue
        _LOGGER.info(
            "Regenerated Cargo lockfile for %s in %.3fs",
            manifest,
            time.perf_counter() - manifest_started_at,
        )
        lockfiles.append(manifest.parent / "Cargo.lock")
    return lockfiles, failures


def _raise_if_failures(
    failures: list[tuple[Path, LockfileRegenerationError]],
) -> None:
    """Raise an aggregated error when one or more manifest updates failed."""
    if not failures:
        return
    # Chain from the first underlying failure so diagnostics (for
    # example a missing cargo executable) survive the aggregation.
    primary = failures[0][1]
    cause = primary.__cause__ if primary.__cause__ is not None else primary
    raise LockfileRegenerationError(
        _build_aggregate_failure_message(failures)
    ) from cause


def _build_aggregate_failure_message(

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
