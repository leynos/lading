"""Unit tests for the in-process metrics accumulator."""

import collections.abc as cabc
import json
import logging
import threading
import typing as typ

import pytest

from lading.utils import metrics

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@pytest.fixture(autouse=True)
def _reset_metrics() -> cabc.Iterator[None]:
    """Isolate the metric registry for each test."""
    metrics.reset()
    yield
    metrics.reset()


def test_increment_accumulates_per_label_set() -> None:
    """Distinct label values accumulate independently."""
    metrics.increment_counter("demo.total", subcommand="package")
    metrics.increment_counter("demo.total", subcommand="package")
    metrics.increment_counter("demo.total", subcommand="publish")

    assert metrics.counter_value("demo.total", subcommand="package") == 2, (
        "two package increments must accumulate to two"
    )
    assert metrics.counter_value("demo.total", subcommand="publish") == 1, (
        "one publish increment must accumulate to one"
    )
    assert metrics.counter_value("demo.total", subcommand="check") == 0, (
        "an unseen label set must report a zero counter"
    )


def test_zero_amount_increment_is_a_noop() -> None:
    """increment_counter with amount=0 must not create a registry entry."""
    metrics.increment_counter("noop.counter", amount=0, label="x")
    assert metrics.counter_value("noop.counter", label="x") == 0, (
        "a zero-amount increment must leave the counter at zero"
    )
    assert not metrics.snapshot(), (
        "a zero-amount increment must not create a registry entry"
    )


def test_zero_amount_increment_does_not_break_quiet_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """emit_summary must stay silent when only zero-amount increments occurred."""
    metrics.increment_counter("noop.counter", amount=0)
    with caplog.at_level(logging.INFO):
        metrics.emit_summary()
    summary_records = [
        record for record in caplog.records if "metrics summary" in record.getMessage()
    ]
    assert summary_records == [], (
        "no metrics summary must be logged when only zero-amount increments occurred"
    )


def test_label_order_does_not_matter() -> None:
    """Label ordering is normalized in the registry key."""
    metrics.increment_counter("demo.pair", a="1", b="2")

    assert metrics.counter_value("demo.pair", b="2", a="1") == 1, (
        "reversing label order must address the same counter"
    )


def test_snapshot_and_reset() -> None:
    """Snapshots copy the registry and reset clears it."""
    metrics.increment_counter("demo.total")

    captured = metrics.snapshot()
    assert captured == {("demo.total", ()): 1}, (
        "the snapshot must capture the unlabelled counter"
    )

    metrics.reset()
    assert not metrics.snapshot(), "reset must clear the registry"
    # The snapshot is a copy, unaffected by the reset.
    assert captured == {("demo.total", ()): 1}, (
        "the captured snapshot must survive a later reset"
    )


def test_emit_summary_logs_structured_payload(
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """The summary line carries a JSON payload of every counter.

    Snapshotting the rendered message locks the exact structured output format
    operators see, including counter ordering and label-key normalization.
    """
    caplog.set_level(logging.INFO, logger="lading.utils.metrics")
    metrics.increment_counter("demo.total", subcommand="package")
    metrics.increment_counter("demo.total", subcommand="package")
    metrics.increment_counter("demo.total", subcommand="publish")
    metrics.increment_counter("demo.pair", b="2", a="1")

    metrics.emit_summary()

    summaries = [
        record.getMessage()
        for record in caplog.records
        if "metrics summary" in record.getMessage()
    ]
    assert len(summaries) == 1, "exactly one metrics summary line must be emitted"
    assert summaries[0] == snapshot, (
        "the rendered summary must match the locked snapshot"
    )


def test_emit_summary_is_silent_without_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Quiet runs do not emit an empty summary line."""
    caplog.set_level(logging.INFO, logger="lading.utils.metrics")

    metrics.emit_summary()

    assert not caplog.records, "a run without metrics must not log a summary line"


def test_observe_duration_aggregates() -> None:
    """Duration observations aggregate count and total seconds."""
    metrics.observe_duration("demo.duration", 0.25, operation="refresh")
    metrics.observe_duration("demo.duration", 0.5, operation="refresh")

    stats = metrics.duration_stats("demo.duration", operation="refresh")
    assert stats.count == 2, "two observations must aggregate to a count of two"
    assert stats.total_seconds == pytest.approx(0.75), (
        "the aggregated duration must sum both observations"
    )
    assert metrics.duration_stats("demo.duration", operation="other").count == 0, (
        "an unseen operation label must report a zero count"
    )


def test_emit_summary_includes_durations(caplog: pytest.LogCaptureFixture) -> None:
    """The summary payload renders duration aggregates alongside counters."""
    caplog.set_level(logging.INFO, logger="lading.utils.metrics")
    metrics.observe_duration("demo.duration", 0.25)

    metrics.emit_summary()

    payload = json.loads(caplog.records[-1].getMessage().partition(": ")[2])
    assert payload == [
        {
            "metric": "demo.duration",
            "labels": {},
            "count": 1,
            "total_seconds": 0.25,
        }
    ], "the summary payload must render the duration aggregate"


def test_emit_summary_snapshot_with_counters_and_durations(
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Counters and duration aggregates serialise together in a stable layout.

    Locks the combined output format so a regression in ordering or in the
    counter-vs-duration field shapes is caught.
    """
    caplog.set_level(logging.INFO, logger="lading.utils.metrics")
    metrics.increment_counter("demo.total", subcommand="package")
    metrics.observe_duration("demo.duration", 0.25, operation="refresh")
    metrics.observe_duration("demo.duration", 1.75, operation="refresh")

    metrics.emit_summary()

    summaries = [
        record.getMessage()
        for record in caplog.records
        if "metrics summary" in record.getMessage()
    ]
    assert len(summaries) == 1, "exactly one combined summary line must be emitted"
    assert summaries[0] == snapshot, (
        "the combined summary must match the locked snapshot"
    )


def test_register_summary_atexit_registers_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap registration installs ``emit_summary`` at most once.

    Registration moved out of module import into explicit bootstrap, so the
    helper must be idempotent across repeated calls (e.g. successive in-process
    CLI invocations during tests).
    """
    registered: list[cabc.Callable[[], None]] = []
    monkeypatch.setattr(metrics, "_summary_hook_registered", threading.Event())
    monkeypatch.setattr(metrics.atexit, "register", registered.append)

    metrics.register_summary_atexit()
    metrics.register_summary_atexit()

    assert registered == [metrics.emit_summary], (
        "repeat registration must install the summary hook exactly once"
    )


def test_register_summary_atexit_leaves_flag_unset_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``atexit.register`` leaves the hook unregistered and retryable."""
    monkeypatch.setattr(metrics, "_summary_hook_registered", threading.Event())

    def boom(_func: cabc.Callable[[], None]) -> None:
        message = "registration failed"
        raise RuntimeError(message)

    monkeypatch.setattr(metrics.atexit, "register", boom)
    with pytest.raises(RuntimeError, match="registration failed"):
        metrics.register_summary_atexit()
    assert not metrics._summary_hook_registered.is_set(), (
        "a failed registration must leave the hook unregistered for retry"
    )

    # With registration working, a later call records the hook exactly once.
    registered: list[cabc.Callable[[], None]] = []
    monkeypatch.setattr(metrics.atexit, "register", registered.append)
    metrics.register_summary_atexit()

    assert registered == [metrics.emit_summary], (
        "the retry after a failed registration must record the hook once"
    )
    assert metrics._summary_hook_registered.is_set(), (
        "a successful registration must mark the hook as registered"
    )


def test_register_summary_atexit_registers_once_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent callers register the hook exactly once.

    The check-and-register is performed under ``_LOCK``, so no interleaving of
    threads can pass the guard twice and register ``emit_summary`` more than
    once.
    """
    monkeypatch.setattr(metrics, "_summary_hook_registered", threading.Event())
    registered: list[cabc.Callable[[], None]] = []
    monkeypatch.setattr(metrics.atexit, "register", registered.append)

    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def worker() -> None:
        # Release all threads together to maximise contention on the guard.
        barrier.wait()
        metrics.register_summary_atexit()

    threads = [threading.Thread(target=worker) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert registered == [metrics.emit_summary], (
        "concurrent callers must register the summary hook exactly once"
    )
