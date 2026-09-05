"""Property tests for the sccache ledger and session invariants (issue #252).

For any bounded sequence of cargo invocations with monotonically growing
sccache counters, and an optional query that fails part-way through:

- the ledger attributes exactly one record per invocation, each record's
  counters equal the difference between consecutive snapshots, and the
  per-invocation deltas sum to the pipeline delta;
- the session's baseline query precedes every other query, every recorded
  invocation is followed by exactly one query, and a failing query ends the
  querying without raising.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from lading.commands import publish_sccache
from lading.commands.publish_sccache_stats import SccacheCounters, SccacheSnapshot

_WRAPPER = Path("/opt/ci-tools/sccache")
_JSON_QUERY = (str(_WRAPPER), "--show-stats", "--stats-format=json")

_counters = st.builds(
    SccacheCounters,
    requests=st.integers(min_value=0, max_value=50),
    hits=st.integers(min_value=0, max_value=50),
    misses=st.integers(min_value=0, max_value=50),
    errors=st.integers(min_value=0, max_value=5),
)
_invocations = st.lists(
    st.tuples(
        st.sampled_from(["alpha", "beta", "gamma"]),
        st.sampled_from(["package", "publish"]),
        st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    ),
    max_size=8,
)


def _cumulative(increments: list[SccacheCounters]) -> list[SccacheCounters]:
    """Return running totals starting from zero, one per increment."""
    totals: list[SccacheCounters] = []
    running = SccacheCounters()
    for increment in increments:
        running = SccacheCounters(
            requests=running.requests + increment.requests,
            hits=running.hits + increment.hits,
            misses=running.misses + increment.misses,
            errors=running.errors + increment.errors,
        )
        totals.append(running)
    return totals


def _snapshot(counters: SccacheCounters) -> SccacheSnapshot:
    return SccacheSnapshot(raw={"stats": counters.as_dict()}, counters=counters)


@given(invocations=_invocations, data=st.data())
@settings(max_examples=150, deadline=None)
def test_ledger_attributes_consecutive_differences(
    invocations: list[tuple[str, str, float]], data: st.DataObject
) -> None:
    """Each record is the difference of consecutive snapshots; they sum to delta."""
    increments = data.draw(
        st.lists(
            _counters, min_size=len(invocations) + 1, max_size=len(invocations) + 1
        )
    )
    snapshots = [_snapshot(total) for total in _cumulative(increments)]
    ledger = publish_sccache.SccacheLedger(baseline=snapshots[0])

    records = [
        ledger.attribute(snapshot, crate=crate, subcommand=subcommand, seconds=secs)
        for snapshot, (crate, subcommand, secs) in zip(
            snapshots[1:], invocations, strict=True
        )
    ]

    assert [record.counters for record in records] == increments[1:], (
        "every record must equal the counters added since the previous snapshot"
    )
    summed = SccacheCounters()
    for record in records:
        summed = SccacheCounters(
            requests=summed.requests + record.counters.requests,
            hits=summed.hits + record.counters.hits,
            misses=summed.misses + record.counters.misses,
            errors=summed.errors + record.counters.errors,
        )
    assert summed == ledger.delta, (
        "per-invocation deltas must sum to the pipeline delta"
    )
    assert ledger.report(_WRAPPER)["crates"] == [record.as_dict() for record in records]


@dc.dataclass
class _SequenceRunner:
    """Runner double serving cumulative counters and failing at one ordinal."""

    totals: list[SccacheCounters]
    failing_query_index: int | None
    calls: list[tuple[str, ...]] = dc.field(default_factory=list)
    _served: int = 0

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, env, echo_stdout
        call = tuple(command)
        self.calls.append(call)
        if call != _JSON_QUERY:
            return 0, "cumulative text\n", ""
        index = self._served
        self._served += 1
        if index == self.failing_query_index:
            return 1, "", "sccache: error"
        total = self.totals[min(index, len(self.totals) - 1)]
        return 0, json.dumps({"stats": _payload_stats(total)}), ""


def _payload_stats(counters: SccacheCounters) -> dict[str, object]:
    return {
        "compile_requests": counters.requests,
        "cache_hits": {"counts": {"Rust": counters.hits}},
        "cache_misses": {"counts": {"Rust": counters.misses}},
        "cache_read_errors": counters.errors,
    }


@given(invocations=_invocations, data=st.data())
@settings(max_examples=150, deadline=None)
def test_session_queries_bracket_invocations_and_stop_on_failure(
    invocations: list[tuple[str, str, float]], data: st.DataObject
) -> None:
    """Baseline first, one query per recorded invocation, none after a failure."""
    query_count = len(invocations) + 1
    increments = data.draw(
        st.lists(_counters, min_size=query_count, max_size=query_count)
    )
    failing_query_index = data.draw(
        st.one_of(st.none(), st.integers(min_value=0, max_value=query_count - 1))
    )
    runner = _SequenceRunner(_cumulative(increments), failing_query_index)
    # No report path, so the session never touches the filesystem.
    session = publish_sccache.SccacheSession(
        wrapper=_WRAPPER, runner=runner, cwd=Path("/unused")
    )

    session.begin()
    for crate, subcommand, seconds in invocations:
        session.record(crate, subcommand, seconds)
    session.finish()

    json_queries = [call for call in runner.calls if call == _JSON_QUERY]
    assert runner.calls[0] == _JSON_QUERY, "the baseline query must come first"
    if failing_query_index is None:
        assert len(json_queries) == query_count, (
            "one baseline query plus one query per invocation"
        )
        assert len(session.records) == len(invocations)
        assert session.enabled
        assert runner.calls[-1] != _JSON_QUERY, "finish() ends with the text mirror"
    else:
        assert len(json_queries) == failing_query_index + 1, (
            "no JSON query may follow the failing one"
        )
        assert len(session.records) == max(failing_query_index - 1, 0), (
            "records stop at the invocation whose query failed"
        )
        assert not session.enabled
        assert all(call == _JSON_QUERY for call in runner.calls), (
            "a disabled session never runs the text mirror"
        )
