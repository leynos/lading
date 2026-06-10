"""Integration tests for lockfile rebuild behaviour in :mod:`lading.commands.bump`."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import pathlib
import typing as typ

import pytest

from lading import config as config_module
from lading.commands import bump
from tests.helpers.workspace_builders import _make_config, _make_workspace

if typ.TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch
    from syrupy.assertion import SnapshotAssertion


@dc.dataclass(frozen=True, slots=True)
class _LockfileSkipScenario:
    """Parameters describing lockfile rebuild skip scenarios."""

    test_id: str
    version: str
    rebuild_lockfiles: bool
    fail_message: str


def test_run_rebuilds_lockfiles_when_enabled(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """Lockfile regeneration runs and is reported when explicitly enabled."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    nested_lockfile = tmp_path / "crates/ui/Cargo.lock"
    captured: dict[str, object] = {}

    def fake_regenerate_lockfiles(
        workspace_root: pathlib.Path,
        lockfile_manifests: tuple[str, ...],
        *,
        runner: object | None = None,
    ) -> tuple[pathlib.Path, ...]:
        captured["calls"] = int(captured.get("calls", 0)) + 1
        captured["workspace_root"] = workspace_root
        captured["lockfile_manifests"] = lockfile_manifests
        captured["runner"] = runner
        return (tmp_path / "Cargo.lock", nested_lockfile)

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fake_regenerate_lockfiles,
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=True,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert captured == {
        "calls": 1,
        "workspace_root": tmp_path,
        "lockfile_manifests": (),
        "runner": None,
    }, "expected a single regenerate_lockfiles call for the workspace root"
    assert message == snapshot


def test_run_skips_lockfiles_when_disabled(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """Lockfile regeneration is suppressed when explicitly disabled."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        lambda *args, **kwargs: pytest.fail(
            "regenerate_lockfiles must not be called when rebuild_lockfiles=False"
        ),
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert message == snapshot


def test_run_inherits_lockfile_rebuild_configuration(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """Programmatic bump calls inherit lockfile rebuild configuration by default."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail("lockfile regeneration should inherit configuration")

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    assert message == snapshot


def test_run_reports_lockfiles_in_dry_run(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """Dry-run bump output reports lockfiles without regenerating them."""
    workspace = _make_workspace(tmp_path)
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(lockfile_manifests=("crates/ui/Cargo.toml",))
    )
    nested_lockfile = tmp_path / "crates/ui/Cargo.lock"
    captured: dict[str, object] = {}

    def fake_resolve_lockfile_paths(
        workspace_root: pathlib.Path,
        lockfile_manifests: tuple[str, ...],
    ) -> tuple[pathlib.Path, ...]:
        captured["workspace_root"] = workspace_root
        captured["lockfile_manifests"] = lockfile_manifests
        return (tmp_path / "Cargo.lock", nested_lockfile)

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail("dry-run lockfile reporting should not invoke Cargo")

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "resolve_lockfile_paths",
        fake_resolve_lockfile_paths,
    )
    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            rebuild_lockfiles=True,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert captured == {
        "workspace_root": tmp_path,
        "lockfile_manifests": ("crates/ui/Cargo.toml",),
    }, "expected dry-run lockfile path resolution for the configured manifest"
    assert message == snapshot


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


@pytest.mark.parametrize(
    "scenario",
    [
        _LockfileSkipScenario(
            test_id="disabled",
            version="1.2.3",
            rebuild_lockfiles=False,
            fail_message="lockfile regeneration should be skipped",
        ),
        _LockfileSkipScenario(
            test_id="versions_already_match",
            version="0.1.0",
            rebuild_lockfiles=True,
            fail_message=(
                "lockfiles should not be regenerated without manifest changes"
            ),
        ),
    ],
    ids=lambda scenario: scenario.test_id,
)
def test_run_skips_lockfile_rebuild(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    scenario: _LockfileSkipScenario,
    snapshot: SnapshotAssertion,
) -> None:
    """Lockfile regeneration is skipped when disabled or no manifests changed."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()

    def fail_regeneration(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail(scenario.fail_message)

    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        fail_regeneration,
    )

    message = bump.run(
        tmp_path,
        scenario.version,
        options=bump.BumpOptions(
            rebuild_lockfiles=scenario.rebuild_lockfiles,
            configuration=configuration,
            workspace=workspace,
        ),
    )

    assert message == snapshot
