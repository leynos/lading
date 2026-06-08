"""Tests for lockfile regeneration after bump operations."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import pathlib
import typing as typ

import pytest

from lading.commands import bump_lockfiles

if typ.TYPE_CHECKING:
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
        runner=runner,
    )

    assert lockfiles == (tmp_path / "Cargo.lock",)
    assert runner.invocations == [
        _Invocation(
            command=(
                "cargo",
                "update",
                "--workspace",
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
            "update",
            "--workspace",
            "--manifest-path",
            str(nested_manifest),
        ),
        cwd=tmp_path,
    )


def test_regenerate_lockfiles_deduplicates_root_manifest(tmp_path: Path) -> None:
    """Explicit root manifest entries should not trigger duplicate rebuilds."""
    runner = _RecordingRunner()

    lockfiles = bump_lockfiles.regenerate_lockfiles(
        tmp_path,
        ("Cargo.toml", "./Cargo.toml", "crates/nested/Cargo.toml"),
        runner=runner,
    )

    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )
    assert [invocation.command for invocation in runner.invocations] == [
        (
            "cargo",
            "update",
            "--workspace",
            "--manifest-path",
            str(tmp_path / "Cargo.toml"),
        ),
        (
            "cargo",
            "update",
            "--workspace",
            "--manifest-path",
            str(tmp_path / "crates/nested/Cargo.toml"),
        ),
    ]


def test_resolve_lockfile_paths_reports_dry_run_targets(tmp_path: Path) -> None:
    """Dry-run reporting can resolve lockfiles without invoking Cargo."""
    lockfiles = bump_lockfiles.resolve_lockfile_paths(
        tmp_path,
        ("Cargo.toml", "crates/nested/Cargo.toml"),
    )

    assert lockfiles == (
        tmp_path / "Cargo.lock",
        tmp_path / "crates/nested/Cargo.lock",
    )


@pytest.mark.parametrize(
    ("manifest", "expected_message"),
    [
        ("../outside/Cargo.toml", "within the workspace"),
        ("Cargo.lock", "Cargo.toml file"),
        ("crates/nested/foo.toml", "Cargo.toml file"),
    ],
)
def test_resolve_lockfile_paths_rejects_invalid_targets(
    tmp_path: Path,
    manifest: str,
    expected_message: str,
) -> None:
    """Configured manifests must stay in-workspace and name Cargo.toml."""
    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match=expected_message
    ):
        bump_lockfiles.resolve_lockfile_paths(tmp_path, (manifest,))


@pytest.mark.parametrize(
    ("manifest", "expected_message"),
    [
        ("../outside/Cargo.toml", "within the workspace"),
        ("Cargo.lock", "Cargo.toml file"),
        ("crates/nested/foo.toml", "Cargo.toml file"),
    ],
)
def test_regenerate_lockfiles_rejects_invalid_targets_without_running_cargo(
    tmp_path: Path,
    manifest: str,
    expected_message: str,
) -> None:
    """Invalid configured manifests should fail before invoking Cargo."""
    runner = _RecordingRunner()

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match=expected_message
    ):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (manifest,),
            runner=runner,
        )

    assert runner.invocations == []


def test_regenerate_lockfiles_surfaces_cargo_failure(tmp_path: Path) -> None:
    """A failing cargo invocation should abort the bump."""
    runner = _RecordingRunner(result=(101, "", "failed to resolve"))

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError, match="failed to resolve"
    ):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (),
            runner=runner,
        )


def test_regenerate_lockfiles_partial_failure_updates_earlier_lockfiles(
    tmp_path: pathlib.Path,
) -> None:
    """First lockfile update commits to disk even when a later one fails.

    This documents the partial-update semantics: regeneration is not atomic.
    """
    nested = tmp_path / "crates" / "sub"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text('[package]\nname = "sub"\nversion = "0.1.0"\n')
    invocations: list[str] = []

    def partial_runner(
        command: cabc.Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
    ) -> tuple[int, str, str]:
        manifest = next((a for a in command if a.endswith("Cargo.toml")), None)
        invocations.append(str(manifest))
        # Fail only on the nested manifest invocation.
        if manifest and "crates" in manifest:
            return (1, "", "simulated cargo failure")
        return (0, "", "")

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError,
        match="simulated cargo failure",
    ):
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            ["crates/sub/Cargo.toml"],
            runner=partial_runner,
        )

    # Root manifest was successfully processed before the failure.
    assert len(invocations) == 2
    assert any("crates" not in inv for inv in invocations), (
        "root manifest must have been invoked first"
    )


def test_regenerate_lockfiles_wraps_runner_exceptions(tmp_path: Path) -> None:
    """Runner exceptions should retain their cause for diagnostics."""

    def failing_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        del command, cwd
        message = "cargo executable not found"
        raise OSError(message)

    with pytest.raises(
        bump_lockfiles.LockfileRegenerationError,
        match="cargo executable not found",
    ) as exc_info:
        bump_lockfiles.regenerate_lockfiles(
            tmp_path,
            (),
            runner=failing_runner,
        )

    assert isinstance(exc_info.value.__cause__, OSError)
