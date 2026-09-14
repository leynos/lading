"""Contract tests for privacy-safe subprocess relay observability."""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ

from lading.runtime.relay_events import RelayEvent
from lading.runtime.subprocess_runner import write_to_sink
from tests.helpers.relay_sinks import (
    _BrokenPipeSink,
    _Cp1252Sink,
    _TextOnlyCp1252Sink,
)

if typ.TYPE_CHECKING:
    import pytest

    LogCaptureFixture = pytest.LogCaptureFixture
else:  # pragma: no cover - typing helpers
    LogCaptureFixture = typ.Any

_EVENT_LOGGER = "lading.runtime.relay_events"


def _relay_event_records(caplog: LogCaptureFixture) -> list[logging.LogRecord]:
    """Return structured relay event records captured from the event logger."""
    return [record for record in caplog.records if record.name == _EVENT_LOGGER]


def _assert_payload_free_event(
    record: logging.LogRecord,
    expected: RelayEvent,
    payload: str,
) -> None:
    """Assert an event has the exact bounded contract and no relay payload."""
    assert record.levelno == logging.INFO
    assert record.msg == "relay observability event: %s"
    assert record.args == (expected,)
    assert dc.asdict(expected) == {
        "operation": "relay_mirror",
        "stream": expected.stream,
        "transition": expected.transition,
        "error_category": expected.error_category,
    }
    assert payload not in record.getMessage()
    assert payload not in str(dc.asdict(expected))


def test_unicode_fallback_emits_one_payload_free_event(
    caplog: LogCaptureFixture,
) -> None:
    """Unicode fallback emits its one bounded stdout transition event."""
    payload = "private child output: ś ń"
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(_Cp1252Sink(), payload, "stdout")

    assert result is not None
    records = _relay_event_records(caplog)
    assert len(records) == 1
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stdout", "text_to_binary", "unicode_encode"),
        payload,
    )


def test_text_only_disablement_emits_one_payload_free_event(
    caplog: LogCaptureFixture,
) -> None:
    """A text-only Unicode rejection emits its one stderr disablement event."""
    payload = "private child output: ś ń"
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(_TextOnlyCp1252Sink(), payload, "stderr")

    assert result is None
    records = _relay_event_records(caplog)
    assert len(records) == 1
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stderr", "disable_mirroring", "unicode_encode"),
        payload,
    )


def test_broken_pipe_emits_one_payload_free_event(
    caplog: LogCaptureFixture,
) -> None:
    """A broken parent pipe emits its one stderr disablement event."""
    payload = "private child output: do not log"
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)

    result = write_to_sink(_BrokenPipeSink(), payload, "stderr")

    assert result is None
    records = _relay_event_records(caplog)
    assert len(records) == 1
    _assert_payload_free_event(
        records[0],
        RelayEvent("relay_mirror", "stderr", "disable_mirroring", "broken_pipe"),
        payload,
    )
