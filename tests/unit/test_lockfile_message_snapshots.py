"""Snapshot tests for lockfile-related CLI output (issue #81).

PR #75 introduced two text outputs that were previously verified only by
substring matching: the ``(lockfile)`` suffix in ``lading bump`` result
messages and the multi-line stale-lockfile error raised by
``lading publish``. These snapshots exercise the public command entry points
and lock in the exact formats.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from lading import config
from lading.commands import bump, lockfile, lockfile_repository, publish
from lading.workspace import WorkspaceGraph

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_SNAPSHOT_WORKSPACE_ROOT = Path("/ws")


class TestBumpLockfileMessages:
    """Snapshot bump messages for root and nested lockfiles."""

    @pytest.mark.parametrize(
        "lockfile_paths",
        [
            pytest.param(
                (Path("Cargo.lock"),),
                id="root",
            ),
            pytest.param(
                (
                    Path("Cargo.lock"),
                    Path("tests/ui_lints/Cargo.lock"),
                ),
                id="nested",
            ),
        ],
    )
    def test_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        snapshot: SnapshotAssertion,
        tmp_path: Path,
        lockfile_paths: tuple[Path, ...],
    ) -> None:
        """The public bump command renders lockfiles relative to the workspace."""
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = []\n\n[workspace.package]\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        workspace = WorkspaceGraph(workspace_root=tmp_path, crates=())

        def fake_regenerate_lockfiles(
            workspace_root: Path,
            lockfile_manifests: tuple[str, ...],
            *,
            runner: object | None = None,
        ) -> tuple[Path, ...]:
            del lockfile_manifests, runner
            return tuple(workspace_root / path for path in lockfile_paths)

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
                configuration=config.LadingConfig(),
                workspace=workspace,
            ),
        )

        assert snapshot == message


@pytest.mark.usefixtures("enable_publish_preflight")
class TestStaleLockfileMessages:
    """Snapshot stale-lockfile errors for one and multiple lockfiles."""

    @pytest.mark.parametrize(
        "lockfiles",
        [
            pytest.param(
                [_SNAPSHOT_WORKSPACE_ROOT / "Cargo.lock"],
                id="single",
            ),
            pytest.param(
                [
                    _SNAPSHOT_WORKSPACE_ROOT / "Cargo.lock",
                    _SNAPSHOT_WORKSPACE_ROOT / "tests" / "ui_lints" / "Cargo.lock",
                ],
                id="multiple",
            ),
        ],
    )
    def test_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        snapshot: SnapshotAssertion,
        tmp_path: Path,
        lockfiles: list[Path],
    ) -> None:
        """The public publish command reports every stale lockfile repair."""
        monkeypatch.setattr(
            lockfile_repository.CargoLockfileInspectionRepository,
            "discover_tracked_lockfiles",
            lambda _repository, _root: tuple(lockfiles),
        )
        monkeypatch.setattr(
            lockfile_repository.CargoLockfileInspectionRepository,
            "validate_lockfile_freshness",
            lambda _repository, _manifest: lockfile.LockfileFreshness(
                is_fresh=False,
                is_stale=True,
                detail="the lock file needs to be updated",
            ),
        )

        def runner(
            command: cabc.Sequence[str],
            *,
            cwd: Path | None = None,
            env: cabc.Mapping[str, str] | None = None,
        ) -> tuple[int, str, str]:
            del command, cwd, env
            return 0, "", ""

        workspace = WorkspaceGraph(workspace_root=tmp_path, crates=())
        with pytest.raises(publish.PublishPreflightError) as excinfo:
            publish.run(
                tmp_path,
                config.LadingConfig(),
                workspace,
                options=publish.PublishOptions(command_runner=runner),
            )

        assert snapshot == str(excinfo.value)
