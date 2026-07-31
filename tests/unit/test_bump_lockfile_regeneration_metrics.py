"""Tests for Cargo lockfile-regeneration metrics."""

from __future__ import annotations

import collections.abc as cabc
from pathlib import Path

import pytest

from lading.commands import bump_lockfile_regeneration, bump_lockfiles
from lading.runtime import CommandSpawnError
from lading.utils import metrics


def _successful_runner(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Return one successful command result."""
    del command, cwd
    return 0, "", ""


def _cargo_failure_runner(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Return one non-zero Cargo result."""
    del command, cwd
    return 101, "", "dependency conflict"


def _spawn_failure_runner(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Raise the expected command-spawn failure."""
    del command, cwd
    command_name = "cargo"
    raise CommandSpawnError(command_name, FileNotFoundError(command_name))


def _runner_value_failure(
    command: cabc.Sequence[str],
    *,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Raise an expected runner value error."""
    del command, cwd
    message = "invalid command value"
    raise ValueError(message)


@pytest.fixture(autouse=True)
def _metrics_registry() -> cabc.Iterator[None]:
    """Reset the in-process metrics registry around each test."""
    metrics.reset()
    yield
    metrics.reset()


def test_regenerate_lockfiles_records_success_count_and_duration(
    tmp_path: Path,
) -> None:
    """Successful regeneration records its lockfile count and total duration."""
    timestamps = iter((10.0, 10.1, 10.2, 10.5))

    lockfiles = bump_lockfile_regeneration.regenerate_lockfiles(
        tmp_path,
        (),
        runner=_successful_runner,
        clock=lambda: next(timestamps),
    )

    assert lockfiles == (tmp_path / "Cargo.lock",)
    assert (
        metrics.counter_value(
            bump_lockfile_regeneration.REGENERATE_METRIC,
            outcome="success",
            cause="none",
        )
        == 1
    )
    duration = metrics.duration_stats(
        bump_lockfile_regeneration.REGENERATE_DURATION_METRIC
    )
    assert duration.count == 1
    assert duration.total_seconds == pytest.approx(0.5)


def _assert_failed_regeneration_records_cause(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
    runner: cabc.Callable[..., tuple[int, str, str]],
    expected_cause: str,
) -> None:
    """Assert regeneration fails and records the expected bounded cause."""
    with pytest.raises(bump_lockfiles.LockfileRegenerationError):
        bump_lockfile_regeneration.regenerate_lockfiles(
            workspace_root,
            lockfile_manifests,
            runner=runner,
        )

    assert (
        metrics.counter_value(
            bump_lockfile_regeneration.REGENERATE_METRIC,
            outcome="failed",
            cause=expected_cause,
        )
        == 1
    )


@pytest.mark.parametrize(
    ("runner", "expected_cause"),
    [
        (_cargo_failure_runner, "cargo_exit"),
        (_spawn_failure_runner, "command_spawn"),
        (_runner_value_failure, "runner_value"),
    ],
)
def test_regenerate_lockfiles_records_failure_cause(
    tmp_path: Path,
    runner: cabc.Callable[..., tuple[int, str, str]],
    expected_cause: str,
) -> None:
    """Expected operational failures increment their bounded cause counter."""
    _assert_failed_regeneration_records_cause(
        tmp_path,
        (),
        runner,
        expected_cause,
    )


@pytest.mark.parametrize(
    "manifest",
    ["../outside/Cargo.toml", "Cargo.lock", "crates/nested/foo.toml"],
)
def test_regenerate_lockfiles_records_validation_failure(
    tmp_path: Path,
    manifest: str,
) -> None:
    """Invalid configured manifests increment the validation-failure counter."""
    _assert_failed_regeneration_records_cause(
        tmp_path,
        (manifest,),
        _successful_runner,
        "validation",
    )


def test_regenerate_lockfiles_records_partial_success_and_failure(
    tmp_path: Path,
) -> None:
    """A partial run counts successful and failed lockfiles separately."""

    def partial_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        del cwd
        return (
            (101, "", "dependency conflict") if "nested" in command[-1] else (0, "", "")
        )

    with pytest.raises(bump_lockfiles.LockfileRegenerationError):
        bump_lockfile_regeneration.regenerate_lockfiles(
            tmp_path,
            ("nested/Cargo.toml",),
            runner=partial_runner,
        )

    assert (
        metrics.counter_value(
            bump_lockfile_regeneration.REGENERATE_METRIC,
            outcome="success",
            cause="none",
        )
        == 1
    )
    assert (
        metrics.counter_value(
            bump_lockfile_regeneration.REGENERATE_METRIC,
            outcome="failed",
            cause="cargo_exit",
        )
        == 1
    )
