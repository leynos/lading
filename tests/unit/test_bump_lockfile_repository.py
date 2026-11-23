"""Integration tests for the injected LockfileRepository port in :mod:`lading.commands.bump`."""  # ruff: ignore[line-too-long] fixed one-line module docstring; wrapping it trips pydocstyle D205/D209

import collections.abc as cabc
import pathlib

import pytest

from lading.commands import bump
from lading.commands.bump_lockfile_planning import LockfilePlan
from tests.helpers.workspace_builders import _make_config, _make_workspace


class _RecordingLockfileRepository:
    """LockfileRepository double recording calls without touching Cargo."""

    def __init__(self) -> None:
        """Start with empty call records for both port operations."""
        self.planned: list[tuple[pathlib.Path, tuple[str, ...]]] = []
        self.regenerated: list[LockfilePlan] = []

    def plan_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> LockfilePlan:
        """Record the plan request and return a single-lockfile plan."""
        self.planned.append((workspace_root, tuple(lockfile_manifests)))
        return LockfilePlan(
            root=workspace_root,
            manifests=(workspace_root / "Cargo.toml",),
            lockfiles=(workspace_root / "Cargo.lock",),
        )

    def regenerate_planned(self, plan: LockfilePlan) -> tuple[pathlib.Path, ...]:
        """Record the regeneration request and return the planned lockfiles."""
        self.regenerated.append(plan)
        return plan.lockfiles

    def resolve_lockfile_paths(
        self, *_args: object, **_kwargs: object
    ) -> tuple[pathlib.Path, ...]:
        """Fail: the bump pipeline no longer projects paths directly."""
        pytest.fail("the bump pipeline must use plan_lockfiles")

    def regenerate_lockfiles(
        self, *_args: object, **_kwargs: object
    ) -> tuple[pathlib.Path, ...]:
        """Fail: the bump pipeline no longer regenerates from raw manifests."""
        pytest.fail("the bump pipeline must use regenerate_planned")


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

    assert repository.planned == [(tmp_path.resolve(), ())], (
        "a real bump must plan lockfiles through the repository port"
    )
    assert [plan.root for plan in repository.regenerated] == [tmp_path.resolve()], (
        "a real bump must regenerate the plan it validated"
    )
    assert "Cargo.lock (lockfile)" in message, (
        "the bump summary must report the regenerated lockfile"
    )


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

    assert repository.planned == [(tmp_path.resolve(), ())], (
        "a dry run must plan lockfile destinations through the repository port"
    )
    assert not repository.regenerated, "a dry run must not regenerate any lockfiles"
