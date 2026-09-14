"""Behaviour of the publish pre-flight when the build checks are skipped."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish, publish_pipeline, publish_preflight
from lading.commands.lockfile import LockfileDiscoveryError
from lading.commands.publish_skip import SkipPreflightDecision, SkipPreflightSource
from lading.utils import metrics

from .conftest import (
    ORIGINAL_PREFLIGHT,
    make_config,
    make_crate,
    make_preflight_config,
    make_workspace,
)

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner

AUX_BUILD_COMMAND = ("cargo", "build", "--package", "lint")


def _recording_runner(
    calls: list[tuple[str, ...]],
) -> CommandRunner:
    """Return a runner that records each command and reports success."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, env, echo_stdout
        calls.append(tuple(command))
        return 0, "", ""

    return runner


def _cargo_subcommands(calls: cabc.Iterable[tuple[str, ...]]) -> set[str]:
    """Return the cargo subcommands present in ``calls``."""
    return {
        command[1] for command in calls if command[0] == "cargo" and len(command) > 1
    }


@dc.dataclass(frozen=True, slots=True)
class _Scenario:
    """One pre-flight run's configuration, override, and dirty-tree policy."""

    configured_skip: bool
    override: SkipPreflightDecision | None = None
    allow_dirty: bool = False
    runner: CommandRunner | None = None
    clock: cabc.Callable[[], float] | None = None


def _failing_runner(
    calls: list[tuple[str, ...]], failing_prefix: tuple[str, ...]
) -> CommandRunner:
    """Return a runner that records commands and fails one matching prefix."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, env, echo_stdout
        recorded = tuple(command)
        calls.append(recorded)
        if recorded[: len(failing_prefix)] == failing_prefix:
            return 1, "", "stub failure"
        return 0, "", ""

    return runner


def _stepped_clock(values: cabc.Sequence[float]) -> cabc.Callable[[], float]:
    """Return a clock yielding ``values`` in order, then repeating the last."""
    remaining = list(values)

    def clock() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


def _run_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: _Scenario,
) -> list[tuple[str, ...]]:
    """Run the real pre-flight over a recording runner and return its commands."""
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    calls: list[tuple[str, ...]] = []
    configuration = make_config(
        preflight=make_preflight_config(
            skip=scenario.configured_skip, aux_build=(AUX_BUILD_COMMAND,)
        )
    )

    request = publish_preflight.PreflightRequest(
        allow_dirty=scenario.allow_dirty,
        configuration=configuration,
        runner=scenario.runner or _recording_runner(calls),
        skip=scenario.override,
        **({} if scenario.clock is None else {"clock": scenario.clock}),
    )
    publish_preflight._run_preflight_checks(root, request)
    return calls


def test_configured_skip_suppresses_the_build_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``[preflight] skip`` stops the auxiliary build, cargo check and cargo test.

    This is the defect the setting exists for: a caller whose job already ran
    the suite must not pay for it a second time.
    """
    calls = _run_preflight(tmp_path, monkeypatch, _Scenario(configured_skip=True))

    assert _cargo_subcommands(calls).isdisjoint({"check", "test"}), (
        f"no pre-flight build command should run when skipped: {calls}"
    )
    assert AUX_BUILD_COMMAND not in calls, (
        "auxiliary builds exist to support the skipped cargo checks"
    )


def test_skip_still_verifies_the_tree_and_the_lockfiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two cheap publication guards survive the skip.

    Skipping is about not repeating the caller's build; a dirty tree or a
    stale ``Cargo.lock`` would still produce a wrong publication.
    """
    calls = _run_preflight(tmp_path, monkeypatch, _Scenario(configured_skip=True))

    assert ("git", "status", "--porcelain") in calls, (
        "the working-tree guard must still run"
    )
    assert any(command[:2] == ("git", "ls-files") for command in calls), (
        "the lockfile freshness guard must still run"
    )


def test_skip_logs_the_source_of_the_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The skip is announced with the input that requested it.

    A publish log that omitted this would read as though the checks passed.
    """
    override = SkipPreflightDecision(skip=True, source=SkipPreflightSource.COMMAND_LINE)

    with caplog.at_level(logging.INFO, logger=publish_preflight.LOGGER.name):
        _run_preflight(
            tmp_path, monkeypatch, _Scenario(configured_skip=False, override=override)
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Skipping" in message
        and SkipPreflightSource.COMMAND_LINE.description in message
        and "cargo test" in message
        for message in messages
    ), f"expected a skip line naming the flag: {messages}"


def test_skip_does_not_claim_an_opt_in_tree_check_ran(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The skip line reports the working-tree check only when it runs.

    The guard is opt-in through ``--forbid-dirty``, so claiming it ran during a
    default publish would give the log false assurance.
    """
    with caplog.at_level(logging.INFO, logger=publish_preflight.LOGGER.name):
        _run_preflight(
            tmp_path, monkeypatch, _Scenario(configured_skip=True, allow_dirty=True)
        )

    skip_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Skipping")
    ]
    assert skip_messages, "expected a skip line"
    assert all("--forbid-dirty" in message for message in skip_messages), (
        f"skip line should name the opt-in guard: {skip_messages}"
    )
    assert all(
        "working-tree and Cargo.lock" not in message for message in skip_messages
    )


def test_skip_records_its_mode_and_source_as_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resolved mode and source reach the metrics registry.

    A publish step that silently stops building is a latency and assurance
    change, so the exit summary has to show which mode ran and why.
    """
    metrics.reset()

    _run_preflight(tmp_path, monkeypatch, _Scenario(configured_skip=True))

    assert (
        metrics.counter_value(
            publish_preflight.PREFLIGHT_METRIC,
            mode="skipped",
            source=str(SkipPreflightSource.CONFIGURATION),
        )
        == 1
    ), "a configured skip should be counted once as skipped"
    observed = metrics.duration_stats(
        publish_preflight.PREFLIGHT_DURATION_METRIC,
        mode="skipped",
        source=str(SkipPreflightSource.CONFIGURATION),
    )
    assert observed.count == 1, "expected one pre-flight duration observation"


def test_executed_preflight_records_the_executed_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pre-flight that runs is counted as executed, not skipped."""
    metrics.reset()

    _run_preflight(tmp_path, monkeypatch, _Scenario(configured_skip=False))

    assert (
        metrics.counter_value(
            publish_preflight.PREFLIGHT_METRIC,
            mode="executed",
            source=str(SkipPreflightSource.CONFIGURATION),
        )
        == 1
    )


def test_default_configuration_runs_the_build_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a skip request the pre-flight behaves exactly as before."""
    calls = _run_preflight(tmp_path, monkeypatch, _Scenario(configured_skip=False))

    assert _cargo_subcommands(calls) >= {"check", "test"}, (
        f"both pre-flight build commands should run: {calls}"
    )
    assert AUX_BUILD_COMMAND in calls, f"the auxiliary build should run: {calls}"


def test_override_reinstates_the_build_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--no-skip-preflight`` beats a workspace that configures the skip."""
    override = SkipPreflightDecision(
        skip=False, source=SkipPreflightSource.COMMAND_LINE
    )

    calls = _run_preflight(
        tmp_path, monkeypatch, _Scenario(configured_skip=True, override=override)
    )

    assert _cargo_subcommands(calls) >= {"check", "test"}, (
        f"both pre-flight build commands should run: {calls}"
    )
    assert AUX_BUILD_COMMAND in calls, f"the auxiliary build should run: {calls}"


def test_publish_run_forwards_the_skip_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``PublishOptions.skip_preflight`` reaches the pre-flight from ``run``.

    The packaging commands still run, so the skip removes only the duplicated
    verification and not the work the publish step exists to do.
    """
    monkeypatch.setattr(publish_preflight, "_run_preflight_checks", ORIGINAL_PREFLIGHT)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(publish_pipeline, "_invoke", _recording_runner(calls))

    publish.run(
        root,
        make_config(),
        workspace,
        options=publish.PublishOptions(
            skip_preflight=SkipPreflightDecision(
                skip=True, source=SkipPreflightSource.COMMAND_LINE
            )
        ),
    )

    assert _cargo_subcommands(calls) == {"package", "publish"}, (
        f"only the packaging commands should run: {calls}"
    )


def test_failed_guard_still_records_the_skipped_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A skipped pre-flight that fails its lockfile guard still counts.

    The metrics are recorded in a ``finally`` block precisely so a failing run
    is visible; a counter that only ever records successes would understate
    how often the pre-flight runs and hide the failures entirely. Lockfile
    discovery raises its own error type, which the counter must survive just
    the same.
    """
    metrics.reset()
    calls: list[tuple[str, ...]] = []
    runner = _failing_runner(calls, ("git", "ls-files"))

    with pytest.raises(LockfileDiscoveryError):
        _run_preflight(
            tmp_path,
            monkeypatch,
            _Scenario(configured_skip=True, runner=runner),
        )

    assert (
        metrics.counter_value(
            publish_preflight.PREFLIGHT_METRIC,
            mode="skipped",
            source=str(SkipPreflightSource.CONFIGURATION),
        )
        == 1
    ), "a failing lockfile guard should still be counted as skipped"


def test_failed_cargo_check_still_records_the_executed_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An executing pre-flight that fails cargo check still counts."""
    metrics.reset()
    calls: list[tuple[str, ...]] = []
    runner = _failing_runner(calls, ("cargo", "check"))

    with pytest.raises(publish_preflight.PublishPreflightError):
        _run_preflight(
            tmp_path,
            monkeypatch,
            _Scenario(configured_skip=False, runner=runner),
        )

    assert (
        metrics.counter_value(
            publish_preflight.PREFLIGHT_METRIC,
            mode="executed",
            source=str(SkipPreflightSource.CONFIGURATION),
        )
        == 1
    ), "a failing cargo check should still be counted as executed"
    observed = metrics.duration_stats(
        publish_preflight.PREFLIGHT_DURATION_METRIC,
        mode="executed",
        source=str(SkipPreflightSource.CONFIGURATION),
    )
    assert observed.count == 1, "a failing pre-flight still records its duration"


def test_recorded_duration_measures_the_injected_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The duration observation is the elapsed time, not an arbitrary value.

    The clock is injected so this is a fact about the metric rather than about
    how fast the machine happened to be.
    """
    metrics.reset()

    _run_preflight(
        tmp_path,
        monkeypatch,
        _Scenario(configured_skip=True, clock=_stepped_clock([10.0, 12.5])),
    )

    observed = metrics.duration_stats(
        publish_preflight.PREFLIGHT_DURATION_METRIC,
        mode="skipped",
        source=str(SkipPreflightSource.CONFIGURATION),
    )
    assert observed.count == 1, "expected one duration observation"
    assert observed.total_seconds == pytest.approx(2.5), (
        "the observation should be the elapsed time the clock reported"
    )


def test_skipped_preflight_still_fails_on_a_dirty_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Skipping the build checks does not silence the working-tree guard.

    The guard is what makes `--forbid-dirty` mean anything, so a skip must not
    turn a dirty workspace into a successful publication.
    """
    calls: list[tuple[str, ...]] = []

    def dirty_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, env, echo_stdout
        recorded = tuple(command)
        calls.append(recorded)
        if recorded[:2] == ("git", "status"):
            return 0, " M lading/cli.py\n", ""
        return 0, "", ""

    with pytest.raises(publish_preflight.PublishPreflightError, match="uncommitted"):
        _run_preflight(
            tmp_path,
            monkeypatch,
            _Scenario(configured_skip=True, runner=dirty_runner),
        )
