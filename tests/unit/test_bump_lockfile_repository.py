"""Integration tests for the injected LockfileRepository port in :mod:`lading.commands.bump`."""  # noqa: E501

from __future__ import annotations

import collections.abc as cabc
import pathlib

from lading.commands import bump
from tests.helpers.workspace_builders import _make_config, _make_workspace


class _RecordingLockfileRepository:
    """LockfileRepository double recording calls without touching Cargo."""

    def __init__(self) -> None:
        self.resolved: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.regenerated: list[tuple[pathlib.Path, tuple[str, ...]]] = []

    def resolve_lockfile_paths(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        self.resolved.append((workspace_root, tuple(lockfile_manifests)))
        return (workspace_root / "Cargo.lock",)

    def regenerate_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> tuple[pathlib.Path, ...]:
        self.regenerated.append((workspace_root, tuple(lockfile_manifests)))
        return (workspace_root / "Cargo.lock",)


def test_run_uses_injected_lockfile_repository(tmp_path: pathlib.Path) -> None:
    """Bump reaches lockfile operations only through the repository port."""
    workspace = _make_workspace(tmp_path)
    repository = _RecordingLockfileRepository()

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            configuration=_make_config(),
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert repository.regenerated == [(tmp_path.resolve(), ())]
    assert repository.resolved == []
    assert "Cargo.lock (lockfile)" in message


def test_dry_run_projects_through_lockfile_repository(
    tmp_path: pathlib.Path,
) -> None:
    """Dry runs project lockfile paths without regenerating."""
    workspace = _make_workspace(tmp_path)
    repository = _RecordingLockfileRepository()

    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            configuration=_make_config(),
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert repository.resolved == [(tmp_path.resolve(), ())]
    assert repository.regenerated == []
