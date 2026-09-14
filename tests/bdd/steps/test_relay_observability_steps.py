"""BDD steps that pin the externally observable relay event contract."""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

from lading.runtime.relay_events import RelayEvent
from lading.runtime.subprocess_runner import write_to_sink
from tests.helpers.relay_sinks import _Cp1252Sink

if typ.TYPE_CHECKING:
    import pytest

    LogCaptureFixture = pytest.LogCaptureFixture
else:  # pragma: no cover - typing helpers
    LogCaptureFixture = typ.Any

_EVENT_LOGGER = "lading.runtime.relay_events"
_FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"
_PAYLOAD = "private child output: ś ń"

scenarios(str(_FEATURES_DIR / "relay_observability.feature"))


def _event_record(records: list[logging.LogRecord]) -> logging.LogRecord:
    """Return the sole relay observability record from a scenario."""
    relay_records = [record for record in records if record.name == _EVENT_LOGGER]
    assert len(relay_records) == 1
    return relay_records[0]


@given(
    "a parent relay sink that cannot encode Unicode",
    target_fixture="relay_sink",
)
def given_cp1252_relay_sink() -> _Cp1252Sink:
    """Provide a parent sink that selects the UTF-8 binary fallback."""
    return _Cp1252Sink()


@when(
    "the stdout relay mirrors Unicode output",
    target_fixture="relay_event_records",
)
def when_stdout_relay_mirrors_unicode(
    caplog: LogCaptureFixture,
    relay_sink: _Cp1252Sink,
) -> list[logging.LogRecord]:
    """Mirror child output while capturing relay observability records."""
    caplog.set_level(logging.INFO, logger=_EVENT_LOGGER)
    write_to_sink(relay_sink, _PAYLOAD, "stdout")
    return caplog.records


@then("one Unicode fallback relay event is emitted")
def then_unicode_fallback_event_is_emitted(
    relay_event_records: list[logging.LogRecord],
) -> None:
    """Assert the fallback transition has exactly the stable field values."""
    record = _event_record(relay_event_records)
    expected = RelayEvent("relay_mirror", "stdout", "text_to_binary", "unicode_encode")
    assert record.levelno == logging.INFO
    assert record.msg == "relay observability event: %s"
    assert record.args == (expected,)
    assert dc.asdict(expected) == {
        "operation": "relay_mirror",
        "stream": "stdout",
        "transition": "text_to_binary",
        "error_category": "unicode_encode",
    }


@then("the relay event excludes the child output")
def then_relay_event_excludes_child_output(
    relay_event_records: list[logging.LogRecord],
) -> None:
    """Assert the rendered event and structured fields omit the child payload."""
    record = _event_record(relay_event_records)
    event = record.args[0]
    assert isinstance(event, RelayEvent)
    assert _PAYLOAD not in record.getMessage()
    assert _PAYLOAD not in str(dc.asdict(event))
