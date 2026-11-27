"""Recording implementation of the bump lockfile repository port."""

import collections.abc as cabc
import pathlib

from lading.commands.bump_lockfile_planning import LockfilePlan


class RecordingLockfileRepository:
    """Record calls to the bump lockfile repository port without using Cargo.

    The double records each planned and regenerated request. An optional
    callback can inspect workspace state immediately before a regeneration
    request is recorded.

    Attributes
    ----------
    planned : list[tuple[pathlib.Path, tuple[str, ...]]]
        Workspace roots and manifest selections passed to planning.
    regenerated : list[tuple[pathlib.Path, tuple[str, ...]]]
        Workspace roots and manifest selections successfully passed to
        regeneration.
    resolved : list[tuple[pathlib.Path, tuple[str, ...]]]
        Legacy projection requests recorded for callers that still use them.
    before_regenerate : collections.abc.Callable or None
        Optional callback invoked before each regeneration request is recorded.
    """

    def __init__(
        self,
        before_regenerate: cabc.Callable[[pathlib.Path, tuple[str, ...]], None]
        | None = None,
    ) -> None:
        """Initialize empty records for both port operations.

        Parameters
        ----------
        before_regenerate : collections.abc.Callable or None, optional
            Callback invoked with the workspace root and selected manifests
            immediately before a regeneration request is recorded. Defaults to
            ``None``. If it raises, the exception propagates and no regeneration
            record is added.
        """
        self.planned: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.regenerated: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.resolved: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.before_regenerate = before_regenerate
        self._plan_requests: dict[int, tuple[pathlib.Path, tuple[str, ...]]] = {}

    def plan_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> LockfilePlan:
        """Record a planning request and return its single-lockfile plan."""
        manifests = tuple(lockfile_manifests)
        self.planned.append((workspace_root, manifests))
        plan = LockfilePlan(
            root=workspace_root,
            manifests=(workspace_root / "Cargo.toml",),
            lockfiles=(workspace_root / "Cargo.lock",),
        )
        self._plan_requests[id(plan)] = (workspace_root, manifests)
        return plan

    def regenerate_planned(
        self,
        plan: LockfilePlan,
    ) -> tuple[pathlib.Path, ...]:
        """Record a regeneration request and return the planned lockfiles."""
        root, manifests = self._plan_requests.get(id(plan), (plan.root, ()))
        if self.before_regenerate is not None:
            self.before_regenerate(root, manifests)
        self.regenerated.append((root, manifests))
        return plan.lockfiles

    def resolve_lockfile_paths(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        """Record a legacy projection request and return the workspace lockfile."""
        self.resolved.append((workspace_root, tuple(lockfile_manifests)))
        return (workspace_root / "Cargo.lock",)

    def regenerate_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        """Record a legacy regeneration request and return the workspace lockfile."""
        manifests = tuple(lockfile_manifests)
        if self.before_regenerate is not None:
            self.before_regenerate(workspace_root, manifests)
        self.regenerated.append((workspace_root, manifests))
        return (workspace_root / "Cargo.lock",)
