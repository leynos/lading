"""Unit tests for per-crate cargo timing in the publish pipeline (issue #251).

Each ``cargo package`` and ``cargo publish`` invocation is timed so a slow
verify build can be attributed to its crate. The success log line carries the
elapsed seconds and every invocation, successful or not, records one
observation under :data:`lading.commands.publish_execution.CARGO_DURATION_METRIC`.
"""

from __future__ import annotations

import collections.abc as cabc
import itertools
import logging
import typing as typ

import pytest

from lading.commands import publish, publish_execution
from lading.utils import metrics

from .conftest import CallTrackingRunner, make_failing_runner

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.workspace import WorkspaceCrate


def _fixed_clock(*ticks: float) -> cabc.Callable[[], float]:
    """Return a clock yielding ``ticks`` in order, then repeating the last."""
    sequence = itertools.chain(ticks, itertools.repeat(ticks[-1]))
    return lambda: next(sequence)


class _TimingCase(typ.NamedTuple):
    action: publish._CrateAction
    live: bool
    subcommand: str
    expected_message: str


_TIMING_CASES = (
    pytest.param(
        _TimingCase(
            action=publish._package_crate,
            live=False,
            subcommand="package",
            expected_message="Successfully packaged crate alpha (1/3) in 2.5s",
        ),
        id="package",
    ),
    pytest.param(
        _TimingCase(
            action=publish._publish_crate,
            live=False,
            subcommand="publish",
            expected_message="Dry-run publish succeeded for crate alpha (1/3) in 2.5s",
        ),
        id="publish-dry-run",
    ),
    pytest.param(
        _TimingCase(
            action=publish._publish_crate,
            live=True,
            subcommand="publish",
            expected_message="Successfully published crate alpha (1/3) in 2.5s",
        ),
        id="publish-live",
    ),
)


@pytest.fixture(autouse=True)
def _metrics_registry() -> cabc.Iterator[None]:
    """Reset the in-process metrics registry around each test."""
    metrics.reset()
    yield
    metrics.reset()


def _alpha_state(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    *,
    live: bool,
) -> tuple[publish._PublicationPipelineState, WorkspaceCrate]:
    """Return the pipeline state and the ``alpha`` crate for one invocation.

    Returns
    -------
    tuple[publish._PublicationPipelineState, WorkspaceCrate]
        A state whose clock advances by 2.5 s across one invocation, and the
        ``alpha`` crate from its plan.
    """
    plan, preparation, _staging_root = publish_plan_and_prep
    alpha = next(crate for crate in plan.publishable if crate.name == "alpha")
    state = publish._PublicationPipelineState(
        plan,
        preparation,
        publish._PublishExecutionOptions(live=live, allow_dirty=True),
        clock=_fixed_clock(10.0, 12.5),
    )
    return state, alpha


@pytest.mark.parametrize("case", _TIMING_CASES)
def test_success_logs_elapsed_seconds_and_records_duration(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    case: _TimingCase,
) -> None:
    """The success line carries the elapsed time and one duration is recorded."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    state, alpha = _alpha_state(publish_plan_and_prep, live=case.live)

    case.action(alpha, state, runner=CallTrackingRunner())

    assert case.expected_message in caplog.messages
    assert metrics.duration_stats(
        publish_execution.CARGO_DURATION_METRIC,
        subcommand=case.subcommand,
        crate="alpha",
    ) == metrics.DurationStats(count=1, total_seconds=2.5)


def test_failed_invocation_still_records_duration(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """A failing cargo package is still attributed to its crate."""
    state, alpha = _alpha_state(publish_plan_and_prep, live=False)

    with pytest.raises(publish.PublishPreflightError):
        publish._package_crate(alpha, state, runner=make_failing_runner(stderr="boom"))

    assert metrics.duration_stats(
        publish_execution.CARGO_DURATION_METRIC, subcommand="package", crate="alpha"
    ) == metrics.DurationStats(count=1, total_seconds=2.5)


def test_raising_runner_still_records_duration(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """A runner that raises (for example a spawn failure) is still timed."""
    state, alpha = _alpha_state(publish_plan_and_prep, live=False)

    def _raising_runner(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        message = "cargo could not be spawned"
        raise publish.PublishPreflightError(message)

    with pytest.raises(publish.PublishPreflightError):
        publish._package_crate(alpha, state, runner=_raising_runner)

    assert metrics.duration_stats(
        publish_execution.CARGO_DURATION_METRIC, subcommand="package", crate="alpha"
    ) == metrics.DurationStats(count=1, total_seconds=2.5)


def test_each_crate_records_its_own_duration(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Durations are keyed by crate, so the summary carries one record each."""
    plan, preparation, _staging_root = publish_plan_and_prep

    publish._package_publishable_crates(
        plan,
        preparation,
        options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
        runner=CallTrackingRunner(),
    )

    for crate in plan.publishable:
        stats = metrics.duration_stats(
            publish_execution.CARGO_DURATION_METRIC,
            subcommand="package",
            crate=crate.name,
        )
        assert stats.count == 1, crate.name
