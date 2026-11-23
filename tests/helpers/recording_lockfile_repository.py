"""Recording implementation of the bump lockfile repository port."""

import collections.abc as cabc
import pathlib


class RecordingLockfileRepository:
    """Record calls to the bump lockfile repository port without using Cargo.

    The double returns the workspace's single ``Cargo.lock`` path and records
    each request. An optional callback can inspect workspace state immediately
    before a regeneration request is recorded.

    Attributes
    ----------
    resolved : list[tuple[pathlib.Path, tuple[str, ...]]]
        Workspace roots and manifest selections passed to resolution.
    regenerated : list[tuple[pathlib.Path, tuple[str, ...]]]
        Workspace roots and manifest selections successfully passed to
        regeneration.
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
        self.resolved: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.regenerated: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.before_regenerate = before_regenerate

    def resolve_lockfile_paths(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        """Record a resolution request and return the workspace lockfile.

        Parameters
        ----------
        workspace_root : pathlib.Path
            Root directory of the workspace whose lockfile is selected.
        lockfile_manifests : collections.abc.Sequence[str]
            Manifest paths whose lockfiles the repository is asked to resolve.

        Returns
        -------
        tuple[pathlib.Path, ...]
            A one-element tuple containing ``workspace_root / "Cargo.lock"``.
        """
        self.resolved.append((workspace_root, tuple(lockfile_manifests)))
        return (workspace_root / "Cargo.lock",)

    def regenerate_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        """Record a regeneration request and return the workspace lockfile.

        Parameters
        ----------
        workspace_root : pathlib.Path
            Root directory of the workspace whose lockfile is regenerated.
        lockfile_manifests : collections.abc.Sequence[str]
            Manifest paths whose lockfiles the repository is asked to
            regenerate.

        Returns
        -------
        tuple[pathlib.Path, ...]
            A one-element tuple containing ``workspace_root / "Cargo.lock"``.

        Notes
        -----
        If ``before_regenerate`` is set, call it with the workspace root and
        selected manifests before appending the regeneration record. Propagate
        any exception it raises without adding a record.
        """
        manifests = tuple(lockfile_manifests)
        if self.before_regenerate is not None:
            self.before_regenerate(workspace_root, manifests)
        self.regenerated.append((workspace_root, manifests))
        return (workspace_root / "Cargo.lock",)
