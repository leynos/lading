"""Tests for lockfile regeneration after bump operations."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from lading.commands import bump_lockfiles
from lading.commands.publish_errors import PublishPreflightError

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """Recorded command invocation."""

    command: tuple[str, ...]
    cwd: Path | None


class _RecordingRunner:
    """Record command invocations and return a configured result."""

    def __init__(
        self,
        result: tuple[int, str, str] = (0, "", ""),
    ) -> None:
        self.result = result
        self.invocations: list[_Invocation] = []

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        """Record one command invocation."""
        self.invocations.append(_Invocation(command=tuple(command), cwd=cwd))
        return self.result


def test_regenerate_lockfiles_includes_workspace_manifest(tmp_path: Path) -> None:
    """The workspace root manifest should always be regenerated."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        (),
        dry_run=False,
        runner=runner,
    )

    assert lockfiles == (tmp_path / "Cargo.lock",)
    assert runner.invocations == [
        _Invocation(
            command=(
                "cargo",
                "generate-lockfile",
                "--manifest-path",
                str(tmp_path / "Cargo.toml"),
            ),
            cwd=tmp_path,
        )
    ]


def test_regenerate_lockfiles_uses_configured_manifests(tmp_path: Path) -> None:
    """Configured nested manifest paths should be passed to Cargo."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        ("crates/nested/Cargo.toml",),
        dry_run=False,
        runner=runner,
    )

    nested_manifest = tmp_path / "crates/nested/Cargo.toml"
    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )
    assert runner.invocations[-1] == _Invocation(
        command=(
            "cargo",
            "generate-lockfile",
            "--manifest-path",
            str(nested_manifest),
        ),
        cwd=tmp_path,
    )


def test_regenerate_lockfiles_skips_runner_for_dry_run(tmp_path: Path) -> None:
    """Dry runs should not invoke Cargo."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        ("crates/nested/Cargo.toml",),
        dry_run=True,
        runner=runner,
    )

    assert lockfiles == ()
    assert runner.invocations == []


def test_resolve_lockfile_paths_reports_dry_run_targets(tmp_path: Path) -> None:
    """Dry-run reporting can resolve lockfiles without invoking Cargo."""
    lockfiles = bump_lockfiles.resolve_lockfile_paths(
        tmp_path,
        ("crates/nested/Cargo.toml",),
    )

    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )


def test_regenerate_lockfiles_surfaces_cargo_failure(tmp_path: Path) -> None:
    """A failing cargo invocation should abort the bump."""
    runner = _RecordingRunner(result=(101, "", "failed to resolve"))

    with pytest.raises(PublishPreflightError, match="failed to resolve"):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (),
            dry_run=False,
            runner=runner,
        )
