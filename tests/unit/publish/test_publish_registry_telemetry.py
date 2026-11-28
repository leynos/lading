"""Tests for the counter and log lines of crates.io archive lookups."""

import logging
import re

import pytest

from lading.commands import publish_registry, publish_registry_reads
from lading.commands.publish_registry import (
    PublishedArchiveUnavailableError,
    fetch_crates_io_digest,
)
from lading.commands.publish_registry_telemetry import ARCHIVE_FETCH_METRIC
from lading.utils import metrics
from tests.helpers.publish_digests import PLAIN_ARCHIVE
from tests.unit.publish.registry_doubles import Script, http_error

_RESULTS = ("succeeded", "not_published", "retried", "unavailable")


@pytest.fixture(autouse=True)
def _fresh_metrics() -> None:
    """Start every test with no recorded metrics."""
    metrics.reset()


def _counts() -> dict[str, int]:
    """Return the fetch counter for every result label."""
    return {
        result: metrics.counter_value(ARCHIVE_FETCH_METRIC, result=result)
        for result in _RESULTS
    }


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ((PLAIN_ARCHIVE,), {"succeeded": 1}),
        ((http_error(404),), {"not_published": 1}),
        ((http_error(503), PLAIN_ARCHIVE), {"retried": 1, "succeeded": 1}),
    ],
    ids=["first_attempt", "not_published", "retried_then_succeeded"],
)
def test_each_settled_attempt_is_counted_with_a_fixed_label(
    steps: tuple[bytes | BaseException, ...], expected: dict[str, int]
) -> None:
    """Every attempt counts once, under one of four fixed results."""
    fetch_crates_io_digest("a", "1.0.0", transport=Script(*steps).transport())

    assert _counts() == {result: expected.get(result, 0) for result in _RESULTS}, (
        "each attempt must count once under its result"
    )


def test_a_retry_logs_its_attempt_elapsed_time_and_budget_left(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The retry warning says which attempt failed, when, and what time is left."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_registry")

    fetch_crates_io_digest(
        "a", "1.0.0", transport=Script(http_error(503), PLAIN_ARCHIVE).transport()
    )

    warning = next(
        r.getMessage() for r in caplog.records if "retrying" in r.getMessage()
    )
    assert re.search(r"attempt 1 of 3 after \d+ ms", warning), "attempt and elapsed"
    assert re.search(r"retrying with \d+ ms of the time budget left", warning), (
        "and the budget left"
    )


def test_giving_up_logs_one_bounded_summary(caplog: pytest.LogCaptureFixture) -> None:
    """A lookup that gives up summarises attempts, time and failure category."""
    caplog.set_level(logging.ERROR, logger="lading.commands.publish_registry")

    with pytest.raises(PublishedArchiveUnavailableError):
        fetch_crates_io_digest(
            "a", "1.0.0", transport=Script(*[http_error(500)] * 3).transport()
        )

    summaries = [
        r.getMessage() for r in caplog.records if "Giving up" in r.getMessage()
    ]
    assert len(summaries) == 1, "exactly one summary per lookup"
    assert re.search(
        r"after 3 attempt\(s\) and \d+ ms \(http_status\)", summaries[0]
    ), "the summary names the attempts, the elapsed time and the category"
    counts = _counts()
    assert (counts["retried"], counts["unavailable"]) == (2, 1), (
        "two retries, then the final failed attempt"
    )
    assert counts["succeeded"] == counts["not_published"] == 0, "nothing settled"


def test_an_oversized_response_is_summarised_as_such(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A refusal raised inside an attempt is still counted and summarised."""
    caplog.set_level(logging.ERROR, logger="lading.commands.publish_registry")
    monkeypatch.setattr(publish_registry, "_MAX_ARCHIVE_BYTES", 16)
    monkeypatch.setattr(publish_registry_reads, "_READ_CHUNK_BYTES", 8)

    with pytest.raises(PublishedArchiveUnavailableError):
        fetch_crates_io_digest("a", "1.0.0", transport=Script(b"x" * 17).transport())

    assert "(oversized_response)" in caplog.text, "the category names the refusal"
    assert _counts()["unavailable"] == 1, "and the attempt counts as unavailable"
