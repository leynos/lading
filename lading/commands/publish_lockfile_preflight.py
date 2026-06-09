"""Lockfile freshness policy for the publish pre-flight.

This module owns the pre-flight's Cargo lockfile domain step: discovering
tracked lockfiles through the
:class:`lading.commands.lockfile.LockfileInspectionRepository` port,
classifying each one, and composing the operator-facing repair message when
any are stale. It is colocated with the rest of the publish pre-flight but
kept separate from :mod:`lading.commands.publish_preflight`, which owns the
cargo/git command orchestration.

The policy holds no command runner: discovery and freshness probing both run
through the injected repository port (issue #82), so this step never knows how
lockfiles are located or validated. The skip policy for a workspace outside
git control also lives here rather than in discovery (issue #79): discovery
raises :class:`lading.commands.lockfile.NotAGitRepositoryError` and this caller
decides that a non-git workspace has nothing to validate.

Examples
--------
```python
from pathlib import Path

from lading.commands.lockfile import CargoLockfileInspectionRepository
from lading.commands.publish_lockfile_preflight import _validate_lockfile_freshness

repository = CargoLockfileInspectionRepository(runner=runner, env=base_env)
# Returns None when every tracked lockfile is fresh (or the workspace is not
# a git repository); raises PublishPreflightError listing repair commands when
# any tracked lockfile is stale.
_validate_lockfile_freshness(Path("."), repository=repository)
```
"""

from __future__ import annotations

import logging
import shlex
import typing as typ

from lading.commands.lockfile import NotAGitRepositoryError
from lading.commands.publish_errors import PublishPreflightError

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from lading.commands.lockfile import LockfileInspectionRepository

LOGGER = logging.getLogger(__name__)


def _collect_stale_lockfiles(
    tracked: cabc.Iterable[Path],
    repository: LockfileInspectionRepository,
) -> list[Path]:
    """Classify tracked lockfiles; raise immediately on error, return stale paths.

    Every tracked lockfile is classified rather than short-circuiting on the
    first stale result (issue #83): the aggregated error message lists each
    stale lockfile with its repair command, so the operator fixes the whole
    workspace in one pass instead of replaying the pre-flight per lockfile.
    The extra ``cargo metadata --locked`` probes are cheap relative to that
    replay loop. Unexpected (non-stale) failures still raise immediately.

    Raises
    ------
    PublishPreflightError
        Raised when a lockfile freshness check fails with an unexpected error.
    """
    stale: list[Path] = []
    for lockfile_path in tracked:
        manifest_path = lockfile_path.parent / "Cargo.toml"
        freshness = repository.validate_lockfile_freshness(manifest_path)
        if freshness.is_fresh:
            continue
        if freshness.is_stale:
            stale.append(lockfile_path)
            continue
        detail = freshness.detail or "cargo metadata --locked failed"
        message = f"Cargo lockfile freshness check failed for {manifest_path}: {detail}"
        LOGGER.error(message)
        raise PublishPreflightError(message)
    return stale


def _build_stale_lockfile_message(stale_lockfiles: list[Path]) -> str:
    """Return a human-readable diagnostic message for stale lockfiles."""
    lines = [
        "Tracked Cargo.lock files are stale after manifest version changes.",
        (
            "This can happen after manifest changes made without `lading bump`, "
            "or after running `lading bump --no-rebuild-lockfiles`; repair each "
            "stale lockfile directly:"
        ),
    ]
    for lockfile_path in stale_lockfiles:
        manifest_path = lockfile_path.parent / "Cargo.toml"
        lines.append(f"- {lockfile_path}")
        quoted_manifest_path = shlex.quote(str(manifest_path))
        lines.append(
            f"  cargo generate-lockfile --manifest-path {quoted_manifest_path}"
        )
    return "\n".join(lines)


def _validate_lockfile_freshness(
    workspace_root: Path,
    *,
    repository: LockfileInspectionRepository,
) -> None:
    """Fail early when tracked Cargo.lock files are stale.

    Discovery and freshness probing run through ``repository`` (the
    :class:`LockfileInspectionRepository` port), so this domain step never
    holds a command runner or knows how lockfiles are located and validated
    (issue #82).
    """
    try:
        tracked = repository.discover_tracked_lockfiles(workspace_root)
    except NotAGitRepositoryError:
        # Skip policy lives here, not in discovery: a non-git workspace has
        # no tracked lockfiles to validate, so pre-flight continues.
        LOGGER.warning(
            "Skipping lockfile freshness validation: %s is not a git repository",
            workspace_root,
        )
        return
    stale_lockfiles = _collect_stale_lockfiles(tracked, repository)

    if not stale_lockfiles:
        LOGGER.info("All %d tracked lockfile(s) are fresh under --locked", len(tracked))
        return

    message = _build_stale_lockfile_message(stale_lockfiles)
    LOGGER.error(message)
    raise PublishPreflightError(message)


__all__ = ["_validate_lockfile_freshness"]
