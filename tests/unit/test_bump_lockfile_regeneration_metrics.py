"""Tests for Cargo lockfile-regeneration metrics."""

import collections.abc as cabc
from pathlib import Path

import pytest

from lading.commands import bump_lockfile_regeneration, bump_lockfiles
from lading.runtime import CommandSpawnError
from lading.utils import metrics

RunnerDouble = cabc.Callable[..., tuple[int, str, str]]


def _result_runner(exit_code: int, stderr: str = "") -> RunnerDouble:
    """Build a runner double that reports one fixed command result.

    Every ``regenerate_lockfiles`` runner shares one keyword-only signature, so
    the doubles are produced from a single factory rather than repeating it.

    Returns
    -------
    RunnerDouble
        A runner reporting ``exit_code`` and ``stderr`` for every call.
    """

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env, echo_stdout
        return exit_code, "", stderr

    return runner


def _raising_runner(failure: cabc.Callable[[], BaseException]) -> RunnerDouble:
    """Build a runner double that raises a freshly built failure on each call."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env, echo_stdout
        raise failure()

    return runner


def _cargo_spawn_failure() -> CommandSpawnError:
    """Build the command-spawn failure raised by a missing Cargo executable."""
    command_name = "cargo"
    return CommandSpawnError(command_name, FileNotFoundError(command_name))


_successful_runner = _result_runner(0)
_cargo_failure_runner = _result_runner(101, "dependency conflict")
_spawn_failure_runner = _raising_runner(_cargo_spawn_failure)
_runner_value_failure = _raising_runner(lambda: ValueError("invalid command value"))


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

    assert lockfiles == (tmp_path / "Cargo.lock",), (
        "regeneration should return the root lockfile"
    )
    assert (
        metrics.counter_value(
            bump_lockfile_regeneration.REGENERATE_METRIC,
            outcome="success",
            cause="none",
        )
        == 1
    ), "successful regeneration should increment the success counter"
    duration = metrics.duration_stats(
        bump_lockfile_regeneration.REGENERATE_DURATION_METRIC
    )
    assert duration.count == 1, "successful regeneration should record one duration"
    assert duration.total_seconds == pytest.approx(0.5), (
        "recorded duration should span the complete regeneration"
    )


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
    ), "failed regeneration should increment the expected cause counter"


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
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, env, echo_stdout
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
    ), "partial regeneration should count the successful root lockfile"
    assert (
        metrics.counter_value(
            bump_lockfile_regeneration.REGENERATE_METRIC,
            outcome="failed",
            cause="cargo_exit",
        )
        == 1
    ), "partial regeneration should count the Cargo-exit failure"
