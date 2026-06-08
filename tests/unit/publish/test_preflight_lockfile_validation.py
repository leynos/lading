"""Unit tests for _validate_lockfile_freshness pre-flight helper."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from lading.commands import lockfile, publish, publish_preflight

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_validate_lockfile_freshness_passes_when_all_lockfiles_are_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fresh tracked lockfiles allow preflight to continue."""
    root_lockfile = tmp_path / "Cargo.lock"
    nested_lockfile = tmp_path / "tests" / "ui_lints" / "Cargo.lock"
    recorded_env: list[cabc.Mapping[str, str] | None] = []

    monkeypatch.setattr(
        publish_preflight,
        "discover_tracked_lockfiles",
        lambda _root, _runner: (root_lockfile, nested_lockfile),
    )

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        recorded_env.append(env)
        return 0, "", ""

    publish_preflight._validate_lockfile_freshness(
        tmp_path,
        runner=runner,
        env={"CARGO_TERM_COLOR": "never"},
    )

    assert recorded_env == [{"CARGO_TERM_COLOR": "never"}] * 2


def test_validate_lockfile_freshness_reports_stale_lockfiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stale lockfiles are collected and reported with repair commands."""
    root_lockfile = tmp_path / "Cargo.lock"
    nested_lockfile = tmp_path / "tests" / "ui_lints" / "Cargo.lock"

    monkeypatch.setattr(
        publish_preflight,
        "discover_tracked_lockfiles",
        lambda _root, _runner: (root_lockfile, nested_lockfile),
    )
    monkeypatch.setattr(
        publish_preflight,
        "validate_lockfile_freshness",
        lambda _manifest, _runner: lockfile.LockfileFreshness(
            is_fresh=False,
            is_stale=True,
            detail=(
                "the lock file Cargo.lock needs to be updated but --locked was passed"
            ),
        ),
    )

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 0, "", ""

    with pytest.raises(
        publish.PublishPreflightError,
        match="Tracked Cargo\\.lock files are stale",
    ) as excinfo:
        publish_preflight._validate_lockfile_freshness(tmp_path, runner=runner, env={})

    message = str(excinfo.value)
    assert str(root_lockfile) in message
    assert str(nested_lockfile) in message
    assert "lading bump" in message
    assert (
        f"cargo generate-lockfile --manifest-path {tmp_path / 'Cargo.toml'}" in message
    )
    assert (
        "cargo generate-lockfile --manifest-path "
        f"{tmp_path / 'tests' / 'ui_lints' / 'Cargo.toml'}"
    ) in message


def test_validate_lockfile_freshness_error_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """Stale lockfile remediation output is locked by snapshot."""
    workspace_root = Path("/workspace root")
    root_lockfile = workspace_root / "Cargo.lock"
    nested_lockfile = workspace_root / "tests" / "ui_lints" / "Cargo.lock"

    monkeypatch.setattr(
        publish_preflight,
        "discover_tracked_lockfiles",
        lambda _root, _runner: (root_lockfile, nested_lockfile),
    )
    monkeypatch.setattr(
        publish_preflight,
        "validate_lockfile_freshness",
        lambda _manifest, _runner: lockfile.LockfileFreshness(
            is_fresh=False,
            is_stale=True,
            detail=(
                "the lock file Cargo.lock needs to be updated but --locked was passed"
            ),
        ),
    )

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 0, "", ""

    with pytest.raises(
        publish.PublishPreflightError,
        match="Tracked Cargo\\.lock files are stale",
    ) as excinfo:
        publish_preflight._validate_lockfile_freshness(
            workspace_root, runner=runner, env={}
        )

    assert str(excinfo.value) == snapshot()


def test_validate_lockfile_freshness_surfaces_cargo_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cargo failures unrelated to stale lockfiles abort with cargo details."""
    root_lockfile = tmp_path / "Cargo.lock"

    monkeypatch.setattr(
        publish_preflight,
        "discover_tracked_lockfiles",
        lambda _root, _runner: (root_lockfile,),
    )
    monkeypatch.setattr(
        publish_preflight,
        "validate_lockfile_freshness",
        lambda _manifest, _runner: lockfile.LockfileFreshness(
            is_fresh=False,
            detail="failed to download registry index",
        ),
    )

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        return 0, "", ""

    with pytest.raises(
        publish.PublishPreflightError,
        match="failed to download registry index",
    ):
        publish_preflight._validate_lockfile_freshness(tmp_path, runner=runner, env={})
