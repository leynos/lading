"""Integration tests for lockfile rebuild behaviour in :mod:`lading.commands.bump`.

The bump pipeline consumes the lockfile repository port's plan and regenerate
methods, so these tests inject a recording repository rather than patching the
lower-level façade helpers. That keeps the assertions on the contract the
pipeline actually uses.
"""

import collections.abc as cabc
import dataclasses as dc
import pathlib
import typing as typ

import pytest

from lading import config as config_module
from lading.commands import bump
from lading.commands.bump_lockfile_planning import LockfilePlan
from tests.helpers.workspace_builders import _make_config, _make_workspace

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@dc.dataclass(slots=True)
class _RecordingRepository:
    """Record lockfile plan and regeneration calls for assertions."""

    lockfiles: tuple[pathlib.Path, ...]
    manifest_path: pathlib.Path
    calls: list[tuple[str, object]] = dc.field(default_factory=list)
    planned_version: str | None = None
    regenerated_version: str | None = None

    def _root_version(self) -> str:
        """Return the root manifest's ``version`` value at this moment."""
        for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("version = "):
                return line.split('"')[1]
        return ""

    def plan_lockfiles(
        self,
        workspace_root: pathlib.Path,
        lockfile_manifests: cabc.Sequence[str],
    ) -> LockfilePlan:
        """Record the planning inputs and return a plan with the lockfiles."""
        self.planned_version = self._root_version()
        self.calls.append(("plan", (workspace_root, tuple(lockfile_manifests))))
        return LockfilePlan(
            root=workspace_root,
            manifests=(workspace_root / "Cargo.toml",),
            lockfiles=self.lockfiles,
        )

    def regenerate_planned(self, plan: LockfilePlan) -> tuple[pathlib.Path, ...]:
        """Record the regenerated plan and return its lockfiles."""
        self.regenerated_version = self._root_version()
        self.calls.append(("regenerate", plan))
        return plan.lockfiles

    def resolve_lockfile_paths(
        self, *_args: object, **_kwargs: object
    ) -> tuple[pathlib.Path, ...]:
        """Fail: the pipeline must not use the legacy projection method."""
        pytest.fail("the pipeline must use plan_lockfiles, not resolve_lockfile_paths")

    def regenerate_lockfiles(
        self, *_args: object, **_kwargs: object
    ) -> tuple[pathlib.Path, ...]:
        """Fail: the pipeline must not use the legacy regeneration method."""
        pytest.fail("the pipeline must use regenerate_planned")


def test_run_rebuilds_lockfiles_when_enabled(
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Lockfile planning and regeneration run when explicitly enabled."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(lockfile_manifests=("crates/ui/Cargo.toml",))
    )
    lockfiles = (tmp_path / "Cargo.lock", tmp_path / "crates/ui/Cargo.lock")
    repository = _RecordingRepository(
        lockfiles=lockfiles,
        manifest_path=tmp_path / "Cargo.toml",
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=True,
            configuration=configuration,
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert repository.planned_version == "0.1.0", (
        "lockfile planning must run before the first manifest write"
    )
    assert repository.regenerated_version == "1.2.3", (
        "lockfile regeneration must run after the manifests carry the new version"
    )
    assert [call[0] for call in repository.calls] == ["plan", "regenerate"], (
        "a live bump must plan before writing and regenerate after"
    )
    plan_call = repository.calls[0][1]
    assert plan_call == (tmp_path, ("crates/ui/Cargo.toml",)), (
        "the plan must receive the canonical root and configured manifests"
    )
    assert message == snapshot, "the bump summary must list the regenerated lockfiles"


def test_run_skips_lockfiles_when_disabled(
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Lockfile work is suppressed when explicitly disabled."""
    workspace = _make_workspace(tmp_path)
    repository = _RecordingRepository(
        lockfiles=(), manifest_path=tmp_path / "Cargo.toml"
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=_make_config(),
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert not repository.calls, (
        "a disabled rebuild must not plan or regenerate lockfiles"
    )
    assert message == snapshot, (
        "a disabled rebuild must report the bump summary without lockfile entries"
    )


def test_run_inherits_lockfile_rebuild_configuration(
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
) -> None:
    """A programmatic bump inherits ``rebuild_lockfiles`` from configuration."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )
    repository = _RecordingRepository(
        lockfiles=(), manifest_path=tmp_path / "Cargo.toml"
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            configuration=configuration,
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert not repository.calls, (
        "the inherited disabled configuration must suppress all lockfile work"
    )
    assert message == snapshot, (
        "the summary must not name lockfiles when regeneration is disabled"
    )


def test_run_reports_lockfiles_in_dry_run(
    tmp_path: pathlib.Path,
    snapshot: SnapshotAssertion,
) -> None:
    """A dry run reports planned lockfiles without regenerating them."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(lockfile_manifests=("crates/ui/Cargo.toml",))
    )
    lockfiles = (tmp_path / "Cargo.lock", tmp_path / "crates/ui/Cargo.lock")
    repository = _RecordingRepository(
        lockfiles=lockfiles,
        manifest_path=tmp_path / "Cargo.toml",
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            rebuild_lockfiles=True,
            configuration=configuration,
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert [call[0] for call in repository.calls] == ["plan"], (
        "a dry run must plan lockfiles but never regenerate them"
    )
    assert (tmp_path / "Cargo.lock").exists() is False, (
        "a dry run must not create a lockfile"
    )
    assert message == snapshot, (
        "the dry-run summary must list the planned lockfile destinations"
    )


@pytest.mark.parametrize(
    ("rebuild_lockfiles", "version"),
    [
        pytest.param(False, "1.2.3", id="disabled"),
        pytest.param(True, "0.1.0", id="versions_already_match"),
    ],
)
def test_run_skips_lockfile_rebuild(
    tmp_path: pathlib.Path,
    version: str,
    snapshot: SnapshotAssertion,
    *,
    rebuild_lockfiles: bool,
) -> None:
    """No lockfile work happens when disabled or when no manifest changes."""
    workspace = _make_workspace(tmp_path)
    repository = _RecordingRepository(
        lockfiles=(), manifest_path=tmp_path / "Cargo.toml"
    )

    message = bump.run(
        tmp_path,
        version,
        options=bump.BumpOptions(
            rebuild_lockfiles=rebuild_lockfiles,
            configuration=_make_config(),
            workspace=workspace,
            lockfile_repository=repository,
        ),
    )

    assert not repository.calls, (
        "a skipped lockfile rebuild must not reach the repository"
    )
    assert message == snapshot, (
        "a skipped lockfile rebuild must leave the bump summary unchanged"
    )
