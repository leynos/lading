"""Unit tests for the in-process metrics accumulator."""

from __future__ import annotations

import collections.abc as cabc
import logging
import threading
import typing as typ

import pytest

from lading.utils import metrics

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

    LogCaptureFixture = pytest.LogCaptureFixture
else:  # pragma: no cover - typing helpers
    LogCaptureFixture = typ.Any


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

    assert metrics.counter_value("demo.total", subcommand="package") == 2
    assert metrics.counter_value("demo.total", subcommand="publish") == 1
    assert metrics.counter_value("demo.total", subcommand="check") == 0


def test_label_order_does_not_matter() -> None:
    """Label ordering is normalised in the registry key."""
    metrics.increment_counter("demo.pair", a="1", b="2")

    assert metrics.counter_value("demo.pair", b="2", a="1") == 1


def test_snapshot_and_reset() -> None:
    """Snapshots copy the registry and reset clears it."""
    metrics.increment_counter("demo.total")

    captured = metrics.snapshot()
    assert captured == {("demo.total", ()): 1}

    metrics.reset()
    assert metrics.snapshot() == {}
    # The snapshot is a copy, unaffected by the reset.
    assert captured == {("demo.total", ()): 1}


def test_emit_summary_logs_structured_payload(
    caplog: LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """The summary line carries a JSON payload of every counter.

    Snapshotting the rendered message locks the exact structured output format
    operators see, including counter ordering and label-key normalisation.
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
    assert len(summaries) == 1
    assert summaries[0] == snapshot


def test_emit_summary_is_silent_without_metrics(
    caplog: LogCaptureFixture,
) -> None:
    """Quiet runs do not emit an empty summary line."""
    caplog.set_level(logging.INFO, logger="lading.utils.metrics")

    metrics.emit_summary()

    assert not caplog.records


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

    assert registered == [metrics.emit_summary]
