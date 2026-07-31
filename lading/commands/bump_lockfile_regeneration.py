"""Regenerate Cargo lockfiles and report failures across all manifests.

This module invokes ``cargo update --workspace`` for each validated manifest,
records partial successes, and aggregates multiple failures after every target
has been attempted. It reuses path validation and the shared regeneration error
from :mod:`lading.commands.bump_lockfile_paths`.

Examples
--------
Regenerate the root and one configured nested lockfile with an injected runner:

```python
regenerate_lockfiles(
    workspace_root,
    ("fixtures/example/Cargo.toml",),
    runner=runner,
)
```
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import shlex
import time
import typing as typ
from pathlib import Path

from lading.commands.bump_lockfile_paths import (
    LockfileRegenerationError,
    resolve_manifest_paths,
)
from lading.runtime import CommandRunner, CommandSpawnError, subprocess_runner
from lading.utils import metrics
from lading.utils.process import with_detail

_LOGGER = logging.getLogger(__name__)

# Metric names (issue #91); documented in docs/developers-guide.md.
REGENERATE_METRIC = "lockfile.regenerate"
REGENERATE_DURATION_METRIC = "lockfile.regenerate.duration"


def regenerate_lockfiles(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
    *,
    runner: CommandRunner | None = None,
    clock: cabc.Callable[[], float] = time.perf_counter,
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
    clock : Callable[[], float], optional
        Monotonic clock used for logging and duration metrics. Defaults to
        :func:`time.perf_counter`; tests may inject a deterministic clock.

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

    Examples
    --------
    A successful stub runner returns the root and configured lockfile paths:

    ```python
    def runner(command, **kwargs):
        return 0, "", ""

    root = Path("/workspace")
    paths = regenerate_lockfiles(
        root,
        ("fixtures/minimal/Cargo.toml",),
        runner=runner,
    )
    [path.as_posix() for path in paths]
    # ["/workspace/Cargo.lock", "/workspace/fixtures/minimal/Cargo.lock"]
    ```
    """
    command_runner = subprocess_runner if runner is None else runner
    started_at = clock()
    try:
        try:
            manifests = resolve_manifest_paths(workspace_root, lockfile_manifests)
        except LockfileRegenerationError:
            metrics.increment_counter(
                REGENERATE_METRIC,
                outcome="failed",
                cause="validation",
            )
            raise
        _LOGGER.info("Regenerating %d Cargo lockfile(s)", len(manifests))
        lockfiles, failures = _collect_lockfile_results(
            manifests, workspace_root, command_runner, clock
        )
        _record_regeneration_results(lockfiles, failures)
        if failures:
            _raise_aggregated_failure(manifests, failures)
    finally:
        elapsed = clock() - started_at
        metrics.observe_duration(REGENERATE_DURATION_METRIC, elapsed)
    _LOGGER.info(
        "Regenerated %d Cargo lockfile(s) in %.3fs",
        len(lockfiles),
        elapsed,
    )
    return tuple(lockfiles)


def _collect_lockfile_results(
    manifests: tuple[Path, ...],
    workspace_root: Path,
    command_runner: CommandRunner,
    clock: cabc.Callable[[], float],
) -> tuple[list[Path], list[tuple[Path, LockfileRegenerationError]]]:
    """Attempt every manifest and partition the outcomes into a pair."""
    lockfiles: list[Path] = []
    failures: list[tuple[Path, LockfileRegenerationError]] = []
    for manifest in manifests:
        result = _attempt_single_lockfile_update(
            workspace_root, manifest, command_runner, clock
        )
        if result.error is not None:
            failures.append((manifest, result.error))
        elif result.lockfile is not None:
            lockfiles.append(result.lockfile)
    return lockfiles, failures


def _record_regeneration_results(
    lockfiles: cabc.Sequence[Path],
    failures: cabc.Sequence[tuple[Path, LockfileRegenerationError]],
) -> None:
    """Record bounded success and failure counters for one regeneration run."""
    metrics.increment_counter(
        REGENERATE_METRIC,
        amount=len(lockfiles),
        outcome="success",
        cause="none",
    )
    for _, error in failures:
        metrics.increment_counter(
            REGENERATE_METRIC,
            outcome="failed",
            cause=_regeneration_failure_cause(error),
        )


def _regeneration_failure_cause(error: LockfileRegenerationError) -> str:
    """Return the bounded operational cause label for a regeneration error."""
    match error.__cause__:
        case CommandSpawnError():
            return "command_spawn"
        case ValueError():
            return "runner_value"
        case _:
            return "cargo_exit"


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


@dc.dataclass(frozen=True, slots=True)
class _LockfileUpdateResult:
    """Outcome of attempting one manifest's lockfile update."""

    lockfile: Path | None
    error: LockfileRegenerationError | None


def _attempt_single_lockfile_update(
    workspace_root: Path,
    manifest: Path,
    command_runner: CommandRunner,
    clock: cabc.Callable[[], float],
) -> _LockfileUpdateResult:
    """Attempt one manifest's lockfile update and return the outcome."""
    manifest_started_at = clock()
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
        clock() - manifest_started_at,
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
    except (CommandSpawnError, ValueError) as exc:
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
