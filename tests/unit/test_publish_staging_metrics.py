"""Metric contracts for the staging boundary.

Staging is the slowest step of a publish, and its cost depends on what
``publish.stage_exclude`` leaves out. These tests pin the two observations that
make a slow or refused staging attributable in the run's metric summary.
"""

import collections.abc as cabc
import shutil
import typing as typ

import pytest

from lading.commands import (
    publish,
    publish_plan,
    publish_stage_exclude,
    publish_staging,
)
from lading.utils import metrics

if typ.TYPE_CHECKING:
    from tests.unit.conftest import PreparationFixtures


@pytest.fixture(autouse=True)
def reset_staging_metrics() -> cabc.Iterator[None]:
    """Start and finish each test with an empty metrics registry."""
    metrics.reset()
    yield
    metrics.reset()


def _plan(
    preparation_fixtures: PreparationFixtures,
) -> publish_plan.PublishPlan:
    """Return a one-crate plan and its build directory."""
    root = preparation_fixtures.tmp_path / "workspace"
    root.mkdir()
    crate = preparation_fixtures.make_crate(root, "alpha")
    workspace = preparation_fixtures.make_workspace(root, crate)
    return publish.plan_publication(workspace, preparation_fixtures.make_config())


def test_staging_records_duration_and_success(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A completed copy is counted once as a success and timed once."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory)

    with publish_staging.staged_workspace(plan, options=options):
        pass

    assert (
        metrics.counter_value(publish_staging.STAGING_METRIC, outcome="success") == 1
    ), "a completed staging must be counted as a success"
    assert metrics.duration_stats(publish_staging.STAGING_DURATION_METRIC).count == 1, (
        "a completed staging must record its duration"
    )


def test_staging_uses_the_injected_clock(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """The duration metric is measured with the clock seam, not wall time."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)
    ticks = iter([100.0, 102.5])

    preparation, _, _ = publish_staging._stage(plan, options, clock=lambda: next(ticks))
    try:
        assert metrics.duration_stats(
            publish_staging.STAGING_DURATION_METRIC
        ).total_seconds == pytest.approx(2.5), (
            "the duration metric must use the injected clock"
        )
    finally:
        shutil.rmtree(preparation.staging_root)


def test_staging_records_an_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A workspace that cannot be read is counted apart from a refusal."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"

    def unreadable(*_args: object, **_kwargs: object) -> None:
        message = "cannot read link"
        raise PermissionError(message)

    monkeypatch.setattr(publish_stage_exclude, "_reject_excluded_manifests", unreadable)
    options = publish.PublishOptions(build_directory=build_directory)

    with (
        pytest.raises(publish_staging.StageExcludeInspectionError),
        publish_staging.staged_workspace(
            plan, options=options, stage_exclude=(".git",)
        ),
    ):
        pass

    assert (
        metrics.counter_value(
            publish_staging.STAGING_METRIC, outcome="inspection_failed"
        )
        == 1
    ), "an unreadable workspace must be counted apart from a refused pattern"
    assert metrics.duration_stats(publish_staging.STAGING_DURATION_METRIC).count == 1, (
        "an inspection failure must still record its duration"
    )


def test_staging_records_a_rejected_exclusion(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A pattern that removes a manifest is counted as rejected, not failed."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory)

    with (
        pytest.raises(publish_staging.PublishPreparationError),
        publish_staging.staged_workspace(
            plan, options=options, stage_exclude=("alpha",)
        ),
    ):
        pass

    assert (
        metrics.counter_value(publish_staging.STAGING_METRIC, outcome="rejected") == 1
    ), "a refused exclusion must be counted apart from a failed copy"
    assert metrics.duration_stats(publish_staging.STAGING_DURATION_METRIC).count == 1, (
        "a refused exclusion must still record its duration"
    )


def test_staging_records_a_failed_copy(
    monkeypatch: pytest.MonkeyPatch,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """An error other than a refused exclusion is counted as failed."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        message = "copy failed"
        raise OSError(message)

    monkeypatch.setattr(publish_staging, "_copy_workspace_tree", fail_copy)
    options = publish.PublishOptions(build_directory=build_directory)

    with (
        pytest.raises(OSError, match="copy failed"),
        publish_staging.staged_workspace(plan, options=options),
    ):
        pass

    assert (
        metrics.counter_value(publish_staging.STAGING_METRIC, outcome="failed") == 1
    ), "a copy error must be counted as a failure"
    assert metrics.duration_stats(publish_staging.STAGING_DURATION_METRIC).count == 1, (
        "a failed copy must still record its duration"
    )
