"""Metric contracts for the staging boundary.

Staging is the slowest step of a publish, and its cost depends on what
``publish.stage_exclude`` leaves out. These tests pin the two observations that
make a slow or refused staging attributable in the run's metric summary.
"""

import collections.abc as cabc
import typing as typ

import pytest

from lading.commands import publish, publish_plan, publish_staging
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
