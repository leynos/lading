"""Unit tests for the lockfile inspection port adapter.

These cover :class:`lading.commands.lockfile_repository`'s git- and
cargo-backed adapter (issue #82): that it binds its runner and environment,
and forwards each call through to the domain operations unchanged.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from lading.commands import lockfile_repository

if typ.TYPE_CHECKING:
    import collections.abc as cabc


# ---------------------------------------------------------------------------
# CargoLockfileInspectionRepository adapter (issue #82)
# ---------------------------------------------------------------------------


# Each recorded call captures (command, cwd, env, echo_stdout).
type _RecordedCall = tuple[
    tuple[str, ...], Path | None, cabc.Mapping[str, str] | None, bool
]


def _recording_runner(
    calls: list[_RecordedCall],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a runner recording each invocation's command, cwd, env, echo_stdout."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        calls.append((tuple(command), cwd, env, echo_stdout))
        return exit_code, stdout, stderr

    return runner


@pytest.fixture
def _cargo_workspace(tmp_path: Path) -> None:
    """Write a minimal root Cargo workspace manifest and empty lockfile."""
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")


class TestCargoLockfileInspectionRepositoryAdapter:
    """Tests for the CargoLockfileInspectionRepository adapter (issue #82)."""

    @pytest.mark.usefixtures("_cargo_workspace")
    def test_adapter_discovers_lockfiles_binding_env(self, tmp_path: Path) -> None:
        """The adapter discovers tracked lockfiles through its bound runner and env."""
        calls: list[_RecordedCall] = []
        base_env = {"CARGO_TERM_COLOR": "never"}
        repository = lockfile_repository.CargoLockfileInspectionRepository(
            runner=_recording_runner(calls, stdout="Cargo.lock\n"),
            env=base_env,
        )

        result = repository.discover_tracked_lockfiles(tmp_path)

        assert result == (tmp_path / "Cargo.lock",), "discovers the tracked lockfile"
        assert calls == [
            (
                ("git", "ls-files", "**/Cargo.lock", "Cargo.lock"),
                tmp_path,
                base_env,
                True,
            )
        ], "git ls-files should receive the bound env"

    def test_adapter_validates_freshness_binding_env(self, tmp_path: Path) -> None:
        """The adapter probes freshness through its bound runner, applying env."""
        manifest_path = tmp_path / "Cargo.toml"
        calls: list[_RecordedCall] = []
        base_env = {"CARGO_TERM_COLOR": "never"}
        repository = lockfile_repository.CargoLockfileInspectionRepository(
            runner=_recording_runner(calls),
            env=base_env,
        )

        result = repository.validate_lockfile_freshness(manifest_path)

        assert result.is_fresh, "probe should report the lockfile fresh"
        assert len(calls) == 1, "one cargo call expected"
        command, cwd, env, echo_stdout = calls[0]
        assert command[:3] == ("cargo", "metadata", "--locked"), "cargo metadata probe"
        assert cwd == manifest_path.parent, "cargo runs in the manifest directory"
        assert env == base_env, "cargo call should receive the bound env"
        assert echo_stdout is True, "echo_stdout defaults to True"

    @pytest.mark.usefixtures("_cargo_workspace")
    def test_adapter_without_env_leaves_runner_env_untouched(
        self, tmp_path: Path
    ) -> None:
        """With no bound env the adapter forwards calls without injecting one."""
        calls: list[_RecordedCall] = []
        repository = lockfile_repository.CargoLockfileInspectionRepository(
            runner=_recording_runner(calls, stdout="Cargo.lock\n"),
        )

        repository.discover_tracked_lockfiles(tmp_path)

        assert calls[0][2] is None, "no env should be injected without a bound env"

    @pytest.mark.usefixtures("_cargo_workspace")
    def test_adapter_honours_injected_manifest_exists(self, tmp_path: Path) -> None:
        """A custom ``manifest_exists`` predicate overrides the filesystem probe."""
        # The _cargo_workspace fixture writes a real manifest/lockfile pair, so the
        # default filesystem probe would include this lockfile. The injected
        # predicate must be what excludes it, so the test fails if the adapter
        # ignores ``manifest_exists``.
        calls: list[_RecordedCall] = []
        probed: list[Path] = []

        def manifest_exists(manifest_path: Path) -> bool:
            probed.append(manifest_path)
            return False

        repository = lockfile_repository.CargoLockfileInspectionRepository(
            runner=_recording_runner(calls, stdout="Cargo.lock\n"),
            manifest_exists=manifest_exists,
        )

        result = repository.discover_tracked_lockfiles(tmp_path)

        assert result == (), "injected predicate should exclude the lockfile"
        assert probed == [tmp_path / "Cargo.toml"], "predicate probed the manifest"

    def test_adapter_bound_runner_forwards_echo_stdout(self, tmp_path: Path) -> None:
        """The env-bound runner forwards ``echo_stdout`` unchanged to the runner."""
        calls: list[_RecordedCall] = []
        base_env = {"CARGO_TERM_COLOR": "never"}
        repository = lockfile_repository.CargoLockfileInspectionRepository(
            runner=_recording_runner(calls),
            env=base_env,
        )

        bound_runner = repository._bound_runner()
        bound_runner(("git", "status"), cwd=tmp_path, echo_stdout=False)

        assert len(calls) == 1, "one forwarded call expected"
        command, cwd, env, echo_stdout = calls[0]
        assert command == ("git", "status"), "command forwarded unchanged"
        assert cwd == tmp_path, "cwd forwarded unchanged"
        # env is still defaulted from the bound base_env when the call omits it.
        assert env == base_env, "env defaulted from the bound base_env"
        assert echo_stdout is False, "echo_stdout forwarded unchanged"
