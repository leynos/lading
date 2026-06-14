"""Unit tests for the in-process metrics accumulator."""

from __future__ import annotations

import collections.abc as cabc
import json
import logging
import operator
import threading
import typing as typ

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.utils import metrics

# ``increment_counter(name, *, amount=1, **labels)`` reserves these keyword
# names, so they cannot be supplied as label keys through the kwargs API.
_RESERVED_LABEL_KEYS = frozenset({"name", "amount"})
_PROP_LABEL_KEY = st.text(min_size=1, max_size=20).filter(
    lambda key: key not in _RESERVED_LABEL_KEYS
)

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


@given(
    pairs=st.lists(
        st.tuples(_PROP_LABEL_KEY, st.text(max_size=20)),
        min_size=1,
        max_size=8,
        unique_by=operator.itemgetter(0),
    ),
    data=st.data(),
)
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_label_order_invariance(
    pairs: list[tuple[str, str]], data: st.DataObject
) -> None:
    """counter_value is invariant under any permutation of label kwargs."""
    metrics.reset()
    metrics.increment_counter("prop.order", **dict(pairs))

    # Read back with the labels supplied in a different (drawn) order.
    permuted = data.draw(st.permutations(pairs))
    assert metrics.counter_value("prop.order", **dict(permuted)) == 1


@given(
    increments=st.lists(
        st.integers(min_value=1, max_value=100), min_size=1, max_size=50
    )
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_accumulation_sums_increments(increments: list[int]) -> None:
    """Counter value equals the sum of all increment amounts."""
    metrics.reset()
    for amount in increments:
        metrics.increment_counter("prop.sum", amount=amount, label="x")
    assert metrics.counter_value("prop.sum", label="x") == sum(increments)


@given(st.integers(min_value=0, max_value=10))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_snapshot_is_immutable_copy(n: int) -> None:
    """Mutations after snapshot() do not affect the captured copy."""
    metrics.reset()
    for i in range(n):
        metrics.increment_counter("prop.snap", i=str(i))
    captured = metrics.snapshot()
    before = dict(captured)

    # Mutate the live registry.
    metrics.increment_counter("prop.snap.extra")
    metrics.reset()

    assert captured == before


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


def test_observe_duration_aggregates() -> None:
    """Duration observations aggregate count and total seconds."""
    metrics.observe_duration("demo.duration", 0.25, operation="refresh")
    metrics.observe_duration("demo.duration", 0.5, operation="refresh")

    stats = metrics.duration_stats("demo.duration", operation="refresh")
    assert stats.count == 2
    assert stats.total_seconds == pytest.approx(0.75)
    assert metrics.duration_stats("demo.duration", operation="other").count == 0


def test_emit_summary_includes_durations(caplog: LogCaptureFixture) -> None:
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
    ]


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
