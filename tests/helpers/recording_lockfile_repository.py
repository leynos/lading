"""Recording implementation of the bump lockfile repository port."""

import collections.abc as cabc
import pathlib


class RecordingLockfileRepository:
    """LockfileRepository double recording calls without touching Cargo."""

    def __init__(
        self,
        before_regenerate: cabc.Callable[[pathlib.Path, tuple[str, ...]], None]
        | None = None,
    ) -> None:
        """Start with empty call records for both port operations."""
        self.resolved: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.regenerated: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.before_regenerate = before_regenerate

    def resolve_lockfile_paths(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        """Record the resolution request and return a single lockfile path."""
        self.resolved.append((workspace_root, tuple(lockfile_manifests)))
        return (workspace_root / "Cargo.lock",)

    def regenerate_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        """Record the regeneration request and return a single lockfile path."""
        manifests = tuple(lockfile_manifests)
        if self.before_regenerate is not None:
            self.before_regenerate(workspace_root, manifests)
        self.regenerated.append((workspace_root, manifests))
        return (workspace_root / "Cargo.lock",)
