"""Contract tests for privacy-safe subprocess relay observability."""

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from types import SimpleNamespace

import pytest

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

from lading.runtime.relay_events import RelayEvent
from lading.runtime.stream_relay import TextSink, write_to_relay_sink
from lading.runtime.subprocess_runner import write_to_sink
from lading.testing import cmd_mox_runner
from tests.helpers.relay_sinks import (
    _BrokenPipeSink,
    _Cp1252Sink,
    _TextOnlyCp1252Sink,
)

_EVENT_LOGGER = "lading.runtime.relay_events"


def _relay_event_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return structured relay event records captured from the event logger."""
    return [record for record in caplog.records if record.name == _EVENT_LOGGER]


def _assert_payload_free_event(
    record: logging.LogRecord,
    expected: RelayEvent,
    payload: str,
) -> None:
    """Assert an event has the exact bounded contract and no relay payload."""
    assert record.levelno == logging.INFO, "relay decision should log at INFO level"
    assert record.msg == "relay observability event: %s", (
        "relay decision should use the stable parameterized message"
    )
    assert record.args == (expected,), "relay decision should carry the expected event"
    assert dc.asdict(expected) == {
        "operation": "relay_mirror",
        "stream": expected.stream,
        "transition": expected.transition,
        "error_category": expected.error_category,
    }, "relay event should contain only the stable bounded fields"
    assert payload not in record.getMessage(), (
        "rendered log must not expose child output"
    )
    assert payload not in str(dc.asdict(expected)), (
        "event fields must not expose child output"
    )


@pytest.mark.parametrize(
    ("sink_factory", "expected"),
    [
        (
            _Cp1252Sink,
            RelayEvent("relay_mirror", "stdout", "text_to_binary", "unicode_encode"),
        ),
        (
            _Cp1252Sink,
            RelayEvent("relay_mirror", "stderr", "text_to_binary", "unicode_encode"),
        ),
        (
            _TextOnlyCp1252Sink,
            RelayEvent("relay_mirror", "stdout", "disable_mirroring", "unicode_encode"),
        ),
        (
            _TextOnlyCp1252Sink,
            RelayEvent("relay_mirror", "stderr", "disable_mirroring", "unicode_encode"),
        ),
        (
            _BrokenPipeSink,
            RelayEvent("relay_mirror", "stdout", "disable_mirroring", "broken_pipe"),
        ),
        (
            _BrokenPipeSink,
            RelayEvent("relay_mirror", "stderr", "disable_mirroring", "broken_pipe"),
        ),
    ],
    ids=[
        "unicode-binary-fallback-stdout",
        "unicode-binary-fallback-stderr",
        "text-only-disable-stdout",
        "text-only-disable-stderr",
        "broken-pipe-stdout",
        "broken-pipe-stderr",
    ],
)
def test_single_decision_emits_one_payload_free_event(
    sink_factory: cabc.Callable[[], TextSink],
    expected: RelayEvent,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Each relay decision emits its one bounded event."""
    payload = "private child output: ś ń"
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(sink_factory(), payload, expected.stream)

    if expected.transition == "text_to_binary":
        assert result is not None, "Unicode fallback should retain a relay sink"
    else:
        assert result is None, "disablement decisions should stop mirroring"
    records = _relay_event_records(caplog)
    assert len(records) == 1, "each relay decision should emit one event"
    _assert_payload_free_event(records[0], expected, payload)
    snapshot_name = f"{expected.transition}-{expected.error_category}-{expected.stream}"
    assert records[0].getMessage() == snapshot(name=snapshot_name), (
        "rendered relay event should match its stable named snapshot"
    )


def test_unicode_fallback_stays_in_binary_mode_without_duplicate_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Later chunks remain UTF-8 bytes without repeating the transition."""
    first_payload = "private child output: ś"
    later_payload = " and continuation: ń"
    payload = first_payload + later_payload
    sink = _Cp1252Sink()
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    active_sink, binary_sink = write_to_relay_sink(sink, None, "stdout", first_payload)
    active_sink, binary_sink = write_to_relay_sink(
        active_sink, binary_sink, "stdout", later_payload
    )

    assert active_sink is sink, "binary fallback should keep the text sink state"
    assert binary_sink is sink.buffer, "binary fallback should remain selected"
    assert sink.buffer.getvalue() == payload.encode("utf-8"), (
        "fallback and subsequent chunks should be exact UTF-8 bytes"
    )
    records = _relay_event_records(caplog)
    assert len(records) == 1, "binary mode should not repeat its transition event"
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stdout", "text_to_binary", "unicode_encode"),
        payload,
    )


@pytest.mark.parametrize("failure_stage", ["write", "flush"])
def test_binary_broken_pipe_disables_mirroring_and_emits_both_events(
    failure_stage: typ.Literal["write", "flush"],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """A binary write or flush failure disables mirroring with bounded events."""
    payload = "private child output: ś"
    sink = _Cp1252Sink(binary_failure_stage=failure_stage)
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(sink, payload, "stdout")

    assert result is None, "binary broken pipe should disable mirroring"
    records = _relay_event_records(caplog)
    assert len(records) == 2, (
        "fallback selection and binary pipe failure should each emit once"
    )
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stdout", "text_to_binary", "unicode_encode"),
        payload,
    )
    _assert_payload_free_event(
        records[1],
        RelayEvent("relay_mirror", "stdout", "disable_mirroring", "broken_pipe"),
        payload,
    )
    if failure_stage == "write":
        assert [record.getMessage() for record in records] == snapshot(
            name="fallback-then-broken-pipe"
        ), "ordered relay decisions should match their stable snapshot"


def test_text_flush_broken_pipe_after_successful_write_disables_mirroring(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful text write followed by a broken flush emits one event."""
    payload = "accepted child output"
    sink = _Cp1252Sink(text_flush_broken_pipe=True)
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(sink, payload, "stdout")

    assert result is None, "a broken text flush should disable mirroring"
    assert sink.buffer.getvalue() == payload.encode("cp1252"), (
        "the successful text write should reach the sink before flush fails"
    )
    records = _relay_event_records(caplog)
    assert len(records) == 1, "text flush failure should emit one event"
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stdout", "disable_mirroring", "broken_pipe"),
        payload,
    )


def test_fallback_text_flush_broken_pipe_disables_mirroring(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken text flush during fallback emits only the pipe decision."""
    payload = "private child output: ś"
    sink = _Cp1252Sink(text_flush_broken_pipe=True)
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(sink, payload, "stderr")

    assert result is None, "fallback text flush failure should disable mirroring"
    records = _relay_event_records(caplog)
    assert len(records) == 1, "fallback flush failure should emit one event"
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stderr", "disable_mirroring", "broken_pipe"),
        payload,
    )


def test_buffered_cmd_mox_output_emits_stream_labelled_events(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Buffered cmd-mox output preserves capture and identifies both streams."""
    stdout_payload = "private stdout output: ś"
    stderr_payload = "private stderr output: ń"
    stdout_sink = _Cp1252Sink()
    stderr_sink = _Cp1252Sink()
    monkeypatch.setattr(cmd_mox_runner.sys, "stdout", stdout_sink)
    monkeypatch.setattr(cmd_mox_runner.sys, "stderr", stderr_sink)
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = cmd_mox_runner._process_cmd_mox_response(
        SimpleNamespace(
            env={},
            stdout=stdout_payload,
            stderr=stderr_payload,
            exit_code=0,
        ),
        streamed=False,
    )

    assert result == (0, stdout_payload, stderr_payload), (
        "buffered processing should retain complete decoded capture"
    )
    assert stdout_sink.buffer.getvalue() == stdout_payload.encode("utf-8"), (
        "buffered stdout should reach its UTF-8 fallback"
    )
    assert stderr_sink.buffer.getvalue() == stderr_payload.encode("utf-8"), (
        "buffered stderr should reach its UTF-8 fallback"
    )
    records = _relay_event_records(caplog)
    assert len(records) == 2, "each buffered stream should emit one event"
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stdout", "text_to_binary", "unicode_encode"),
        stdout_payload,
    )
    _assert_payload_free_event(
        records[1],
        RelayEvent("relay_mirror", "stderr", "text_to_binary", "unicode_encode"),
        stderr_payload,
    )
