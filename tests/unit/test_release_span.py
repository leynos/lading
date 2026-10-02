"""Tests for the uploader's span record.

Two properties matter and neither is observable from the happy path alone: the
span must close on *every* exit -- success, non-zero status, and exception --
and every attribute must be bounded, so no argument, tag, path, or captured
output can reach a field however the invocation fails. The tests below drive
each exit and then assert on the parsed record, so a field that started
carrying data would fail the boundedness assertion rather than being found in
production.

Two of them compare the serialized *line* against a Syrupy snapshot rather
than against the parsed mapping. The others assert that each field is present,
bounded, and agrees with the exit that produced it; a snapshot additionally
pins the wire format a log consumer parses -- the ``release_span`` prefix, the
JSON key names, and the empty-string status an unobserved exit carries. The
clock is injected, so the duration is exact rather than merely redacted, and no
path, filename, tag, or captured output appears in a record to be leaked into
the snapshot.
"""

import collections.abc as cabc
import io
import json
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.script_imports import import_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import types

    from syrupy.assertion import SnapshotAssertion

SPAN_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_span.py"


def _ticks() -> cabc.Callable[[], float]:
    """Return a clock reading 100.25 seconds after 100.0 exactly once.

    Built per test rather than shared, because an exhausted iterator would
    raise ``StopIteration`` inside the span and turn a passing test into an
    error that looks like a defect in the code under test.

    Returns
    -------
    cabc.Callable[[], float]
        The clock.
    """
    readings = iter([100.0, 100.25])
    return lambda: next(readings)


@pytest.fixture(name="span_module")
def span_module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the span module the way the script imports it.

    Returns
    -------
    types.ModuleType
        The imported ``release_span`` module.
    """
    return import_script_module(monkeypatch, "release_span")


def _span_line(sink: io.StringIO) -> str:
    """Return the single span line written to ``sink``.

    Returns
    -------
    str
        The whole line, prefix included.
    """
    lines = [
        line
        for line in sink.getvalue().splitlines()
        if line.startswith("release_span ")
    ]
    assert len(lines) == 1, f"expected one span line, found {lines}"
    return lines[0]


def _record(sink: io.StringIO) -> dict[str, object]:
    """Parse the single span line written to ``sink``.

    Returns
    -------
    dict[str, object]
        The decoded record.
    """
    return json.loads(_span_line(sink).removeprefix("release_span "))


def test_a_successful_invocation_reports_its_status_and_duration(
    span_module: types.ModuleType,
) -> None:
    """A clean exit is recorded with the status and the elapsed time."""
    sink = io.StringIO()

    with span_module.record_gh_span(sink, clock=_ticks()) as set_exit:
        set_exit(0)

    record = _record(sink)
    assert record["exit_code"] == 0, record
    assert record["failure_category"] == "none", record
    assert record["duration_seconds"] == 0.25, record


def test_a_non_zero_exit_is_reported_without_raising(
    span_module: types.ModuleType,
) -> None:
    """A rejected ``gh`` is a recorded outcome, not an exception.

    The caller decides what a non-zero status means; the span's job is only to
    say it happened, so this must not raise where a success does not.
    """
    sink = io.StringIO()

    with span_module.record_gh_span(sink, clock=_ticks()) as set_exit:
        set_exit(1)

    record = _record(sink)
    assert record["exit_code"] == 1, record
    assert record["failure_category"] == "non-zero-exit", record


def test_an_exception_still_closes_the_span_and_propagates(
    span_module: types.ModuleType,
) -> None:
    """The span closes on the failure path, and the failure still surfaces.

    Recording the span must not swallow the exception: a boundary that turned a
    crash into a silent success would be worse than no boundary at all.
    """
    sink = io.StringIO()

    def explode() -> None:
        message = "the process edge raised"
        raise RuntimeError(message)

    with (
        pytest.raises(RuntimeError, match="the process edge raised"),
        span_module.record_gh_span(sink, clock=_ticks()),
    ):
        explode()

    record = _record(sink)
    assert record["failure_category"] == "raised", record
    assert not record["exit_code"], record


def test_a_missing_status_is_not_reported_as_success(
    span_module: types.ModuleType,
) -> None:
    """A context that exits without a status is not counted as a clean run.

    This is the inverse of the exception case and the easier one to get wrong:
    the default value of the status would read as zero, so a caller that
    returned without reporting would be logged as a success.
    """
    sink = io.StringIO()

    with span_module.record_gh_span(sink, clock=_ticks()):
        pass

    record = _record(sink)
    assert not record["exit_code"], record
    assert record["failure_category"] != "none", record


def test_no_field_can_carry_data_from_the_invocation(
    span_module: types.ModuleType,
) -> None:
    """Every attribute is bounded, whatever the invocation looked like.

    The failure this prevents is a span that grows with the data it carries:
    an argv naming a path, a tag, or a credential must not reach a field. The
    assertion is on the field set and the value types, so adding an unbounded
    field fails here rather than in a dashboard.
    """
    sink = io.StringIO()

    with span_module.record_gh_span(sink, clock=_ticks()) as set_exit:
        set_exit(0)

    record = _record(sink)
    assert set(record) == {
        "operation",
        "schema",
        "duration_seconds",
        "exit_code",
        "failure_category",
    }, record
    assert isinstance(record["operation"], str), record
    assert record["operation"], record
    assert record["operation"].startswith("gh."), (
        f"the operation name is {record['operation']!r}, which is not bounded"
    )
    assert record["failure_category"] in {"none", "non-zero-exit", "raised"}, record
    assert isinstance(record["schema"], int), record


def test_the_failure_category_set_is_closed(span_module: types.ModuleType) -> None:
    """The category is an enum, so a counter built on it stays bounded.

    A free-text reason -- an exception message, a line of ``gh``'s stderr --
    names a path or a token as often as not, so it must not become a label.
    """
    categories = {str(member) for member in span_module.FailureCategory}

    assert categories == {"none", "non-zero-exit", "raised"}, categories
    assert all(json.dumps(category) == f'"{category}"' for category in categories), (
        "every category must serialize as itself"
    )


def test_a_successful_invocation_serializes_to_the_expected_line(
    span_module: types.ModuleType,
    snapshot: SnapshotAssertion,
) -> None:
    """The emitted line is the wire format a log consumer parses.

    The field assertions above read the decoded mapping, which cannot notice a
    change to the encoding they decode through: renaming a JSON key or dropping
    the ``release_span`` prefix would leave every one of them passing while
    every consumer's parser broke. This pins the line itself.

    The duration is exact rather than redacted because the clock is injected --
    there is nothing volatile to erase.
    """
    sink = io.StringIO()

    with span_module.record_gh_span(sink, clock=_ticks()) as set_exit:
        set_exit(0)

    line = _span_line(sink)
    assert line == snapshot(), (
        "a successful invocation must serialize to the pinned span line"
    )
    assert line.startswith("release_span {"), (
        "every span line must open with the release_span prefix"
    )


def test_an_unobserved_status_serializes_as_an_empty_string(
    span_module: types.ModuleType,
    snapshot: SnapshotAssertion,
) -> None:
    """A span that closed without a status serializes to the expected line.

    The empty string is the record's own convention for "no status observed"
    and the one field whose encoding is a deliberate choice rather than a
    direct dump. ``None`` would serialize as JSON's ``null``, which a consumer
    reads as a reported absence rather than an unreported one, so the choice is
    worth pinning where a change to it is visible.
    """
    sink = io.StringIO()

    with span_module.record_gh_span(sink, clock=_ticks()):
        pass

    line = _span_line(sink)
    assert line == snapshot(), (
        "an unobserved status must serialize to the pinned span line"
    )
    assert '"exit_code": ""' in line, (
        "an unobserved status must serialize as an empty string, not null"
    )
