"""Metric contracts for the staging boundary.

Staging is the slowest step of a publish, and its cost depends on what
``publish.stage_exclude`` leaves out. These tests pin the two observations that
make a slow or refused staging attributable in the run's metric summary.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import os
import shutil
import typing as typ
from pathlib import Path

import pytest

from lading.commands import (
    publish,
    publish_plan,
    publish_stage_exclude,
    publish_staging,
    publish_staging_telemetry,
)
from lading.utils import metrics

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

    from tests.unit.conftest import PreparationFixtures


@pytest.fixture(autouse=True)
def reset_staging_metrics() -> cabc.Iterator[None]:
    """Start and finish each test with an empty metrics registry."""
    metrics.reset()
    yield
    metrics.reset()


@dc.dataclass(frozen=True, slots=True)
class _RecordingRecorder:
    """Collect the records the staging boundary hands to its recorder."""

    records: list[tuple[str, float]] = dc.field(default_factory=list)

    def record(self, *, outcome: str, duration_seconds: float) -> None:
        """Append one recorded attempt."""
        self.records.append((outcome, duration_seconds))


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
        metrics.counter_value(
            publish_staging_telemetry.STAGING_METRIC, outcome="success"
        )
        == 1
    ), "a completed staging must be counted as a success"
    assert (
        metrics.duration_stats(publish_staging_telemetry.STAGING_DURATION_METRIC).count
        == 1
    ), "a completed staging must record its duration"


def test_staging_uses_the_injected_clock(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """The recorded duration is measured with the clock seam, not wall time."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)
    ticks = iter([100.0, 102.5])
    recorder = _RecordingRecorder()

    telemetry = publish_staging_telemetry.StagingTelemetry(
        clock=lambda: next(ticks), recorder=recorder
    )
    with publish_staging.staged_workspace(
        plan, options=options, telemetry=telemetry
    ) as staged:
        pass

    assert recorder.records == [("success", pytest.approx(2.5))], (
        "the recorded duration must use the injected clock"
    )
    assert staged.staging_root.exists(), "cleanup=False must retain the tree"
    shutil.rmtree(staged.staging_root)


def test_staging_records_an_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A workspace that cannot be read is counted apart from a refusal."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"

    def unreadable(*_args: object, **_kwargs: object) -> None:
        """Fail as an unreadable workspace does during link inspection."""
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
            publish_staging_telemetry.STAGING_METRIC, outcome="inspection_failed"
        )
        == 1
    ), "an unreadable workspace must be counted apart from a refused pattern"
    assert (
        metrics.duration_stats(publish_staging_telemetry.STAGING_DURATION_METRIC).count
        == 1
    ), "an inspection failure must still record its duration"


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
        metrics.counter_value(
            publish_staging_telemetry.STAGING_METRIC, outcome="rejected"
        )
        == 1
    ), "a refused exclusion must be counted apart from a failed copy"
    assert (
        metrics.duration_stats(publish_staging_telemetry.STAGING_DURATION_METRIC).count
        == 1
    ), "a refused exclusion must still record its duration"


def test_staging_records_a_failed_copy(
    monkeypatch: pytest.MonkeyPatch,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """An error other than a refused exclusion is counted as failed."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        """Fail as the workspace copy does on an I/O error."""
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
        metrics.counter_value(
            publish_staging_telemetry.STAGING_METRIC, outcome="failed"
        )
        == 1
    ), "a copy error must be counted as a failure"
    assert (
        metrics.duration_stats(publish_staging_telemetry.STAGING_DURATION_METRIC).count
        == 1
    ), "a failed copy must still record its duration"


def test_staging_records_a_copy_io_failure_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A copy failure wrapped as a staging error is still counted as failed."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"

    def deny_copy(*_args: object, **_kwargs: object) -> None:
        """Fail as an unreadable source file does during the copy."""
        error = PermissionError("permission denied")
        raise error

    monkeypatch.setattr(shutil, "copytree", deny_copy)
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)

    with (
        pytest.raises(publish_staging.PublishPreparationError),
        publish_staging.staged_workspace(plan, options=options),
    ):
        pass

    assert (
        metrics.counter_value(
            publish_staging_telemetry.STAGING_METRIC, outcome="failed"
        )
        == 1
    ), "a wrapped copy I/O failure must not be counted as a refused exclusion"


def test_staging_records_an_unreadable_workspace(
    monkeypatch: pytest.MonkeyPatch,
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A manifest that cannot be inspected is counted as an inspection failure."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    manifest = plan.workspace_root / "alpha" / "Cargo.toml"
    real_lstat = Path.lstat

    def deny_link(self: Path) -> os.stat_result:
        """Fail the no-follow inspection of the crate manifest."""
        if self == manifest:
            error = PermissionError("permission denied")
            raise error
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", deny_link)
    options = publish.PublishOptions(build_directory=build_directory)

    with (
        pytest.raises(publish_staging.StageExcludeInspectionError),
        publish_staging.staged_workspace(plan, options=options),
    ):
        pass

    assert (
        metrics.counter_value(
            publish_staging_telemetry.STAGING_METRIC, outcome="inspection_failed"
        )
        == 1
    ), "an unreadable manifest must be counted apart from a refused pattern"
    assert (
        metrics.duration_stats(publish_staging_telemetry.STAGING_DURATION_METRIC).count
        == 1
    ), "an inspection failure must still record its duration"


class _FailingRecorder:
    """Recorder double whose storage is unavailable."""

    def record(self, *, outcome: str, duration_seconds: float) -> None:
        """Fail as an unavailable metrics backend does."""
        del outcome, duration_seconds
        message = "recorder unavailable"
        raise RuntimeError(message)


def test_a_failing_recorder_does_not_break_staging(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A telemetry failure must not fail a staging that succeeded."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory)
    telemetry = publish_staging_telemetry.StagingTelemetry(recorder=_FailingRecorder())

    with publish_staging.staged_workspace(
        plan, options=options, telemetry=telemetry
    ) as staged:
        assert staged.staging_root.is_dir(), "the copy must complete"

    assert not staged.staging_root.exists(), "cleanup must still remove the tree"


def _clock_failing_after_start(reads: cabc.Iterator[float]) -> cabc.Callable[[], float]:
    """Return a clock that yields one start time and then fails."""

    def fail_after_start() -> float:
        """Return the start time once, then fail as an exhausted clock does."""
        try:
            return next(reads)
        except StopIteration:
            message = "clock unavailable"
            raise RuntimeError(message) from None

    return fail_after_start


def test_a_clock_that_fails_on_the_second_read_keeps_the_refusal(
    preparation_fixtures: PreparationFixtures,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clock that fails after the start must not replace a refusal."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory)
    telemetry = publish_staging_telemetry.StagingTelemetry(
        clock=_clock_failing_after_start(iter([100.0]))
    )

    with (
        pytest.raises(publish_staging.PublishPreparationError),
        publish_staging.staged_workspace(
            plan, options=options, stage_exclude=("alpha",), telemetry=telemetry
        ),
    ):
        pass

    assert any(
        "telemetry failed" in record.getMessage() for record in caplog.records
    ), "the clock failure must be logged rather than replace the refusal"


def test_a_clock_that_fails_on_the_second_read_completes_the_copy(
    preparation_fixtures: PreparationFixtures,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clock that fails after the start must not stop the copy."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)
    telemetry = publish_staging_telemetry.StagingTelemetry(
        clock=_clock_failing_after_start(iter([100.0]))
    )

    with publish_staging.staged_workspace(
        plan, options=options, telemetry=telemetry
    ) as staged:
        assert staged.staging_root.is_dir(), "the copy must complete"

    assert any(
        "telemetry failed" in record.getMessage() for record in caplog.records
    ), "the clock failure must be logged"
    shutil.rmtree(staged.staging_root)


def test_a_build_directory_inside_the_workspace_is_rejected(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A staging path inside the workspace is a refused configuration."""
    plan = _plan(preparation_fixtures)
    build_directory = plan.workspace_root / "staging"
    options = publish.PublishOptions(build_directory=build_directory)

    with (
        pytest.raises(publish_staging.PublishPreparationError),
        publish_staging.staged_workspace(plan, options=options),
    ):
        pass

    assert (
        metrics.counter_value(
            publish_staging_telemetry.STAGING_METRIC, outcome="rejected"
        )
        == 1
    ), "an unsafe staging path is a configuration refusal, not a copy failure"
    assert (
        metrics.duration_stats(publish_staging_telemetry.STAGING_DURATION_METRIC).count
        == 1
    ), "the refusal must still record its duration"


def test_a_failing_clock_does_not_block_staging(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """A broken clock must not stop the copy from running."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)

    def broken_clock() -> float:
        """Fail as an unavailable monotonic clock does."""
        message = "clock unavailable"
        raise RuntimeError(message)

    telemetry = publish_staging_telemetry.StagingTelemetry(clock=broken_clock)

    with publish_staging.staged_workspace(
        plan, options=options, telemetry=telemetry
    ) as staged:
        assert staged.staging_root.is_dir(), "the copy must run without a clock"

    shutil.rmtree(staged.staging_root)


def test_a_failing_recorder_does_not_replace_the_staging_error(
    preparation_fixtures: PreparationFixtures,
) -> None:
    """The staging error must survive a recorder that also fails."""
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory)
    telemetry = publish_staging_telemetry.StagingTelemetry(recorder=_FailingRecorder())

    with (
        pytest.raises(publish_staging.PublishPreparationError),
        publish_staging.staged_workspace(
            plan, options=options, stage_exclude=("alpha",), telemetry=telemetry
        ),
    ):
        pass


def test_staging_summary_payload_is_pinned(
    preparation_fixtures: PreparationFixtures,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """The exit summary keeps the exact staging records operators read.

    The duration comes from the injected clock, so the snapshot pins the
    payload shape rather than wall time.
    """
    plan = _plan(preparation_fixtures)
    build_directory = preparation_fixtures.tmp_path / "build"
    options = publish.PublishOptions(build_directory=build_directory, cleanup=False)
    ticks = iter([100.0, 102.5])

    telemetry = publish_staging_telemetry.StagingTelemetry(clock=lambda: next(ticks))
    with publish_staging.staged_workspace(
        plan, options=options, stage_exclude=("venv",), telemetry=telemetry
    ) as staged:
        staged_root = staged.staging_root
    try:
        caplog.set_level(logging.INFO, logger="lading.utils.metrics")
        metrics.emit_summary()
    finally:
        shutil.rmtree(staged_root)

    summaries = [
        record.getMessage()
        for record in caplog.records
        if "metrics summary" in record.getMessage()
    ]
    assert len(summaries) == 1, "exactly one metrics summary line must be emitted"
    assert summaries[0] == snapshot, (
        "the staging summary payload must match the locked snapshot"
    )
