"""Then-step definitions for sccache instrumentation.

With compiler-cache statistics on, lading queries the wrapper around every
cargo invocation and writes a JSON report. These steps assert the query order
and the report's contents.
"""

import json

from pytest_bdd import parsers, then

from .cli_run_types import CliRunResult
from .test_publish_infrastructure import _PreflightInvocationRecorder

_SCCACHE_JSON_QUERY = ("--show-stats", "--stats-format=json")
_SCCACHE_TEXT_QUERY = ("--show-stats",)
_CARGO_BUILD_LABELS = frozenset({"cargo::package", "cargo::publish"})


@then(
    "sccache statistics were queried before the first cargo package and after "
    "every cargo invocation"
)
def then_sccache_queries_bracket_cargo_invocations(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert the baseline, per-invocation, and final sccache queries.

    The recorder lists every stubbed invocation in call order, so the
    expected shape is: one JSON query, then for each cargo package/publish a
    cargo call followed by a JSON query, then one plain ``--show-stats``.
    """
    sequence = [
        (label, tuple(args))
        for label, args, _env in preflight_recorder.records
        if label == "sccache" or label in _CARGO_BUILD_LABELS
    ]
    cargo_calls = [entry for entry in sequence if entry[0] in _CARGO_BUILD_LABELS]
    assert cargo_calls, "expected cargo package/publish invocations"
    expected: list[tuple[str, tuple[str, ...]]] = [("sccache", _SCCACHE_JSON_QUERY)]
    for entry in cargo_calls:
        expected.extend((entry, ("sccache", _SCCACHE_JSON_QUERY)))
    expected.append(("sccache", _SCCACHE_TEXT_QUERY))
    assert sequence == expected, f"unexpected query order:\n{sequence}"


@then(parsers.parse('the compiler-cache report "{name}" lists every cargo invocation'))
def then_sccache_report_lists_every_invocation(
    cli_run: CliRunResult,
    preflight_recorder: _PreflightInvocationRecorder,
    name: str,
) -> None:
    """Assert the JSON report carries one record per cargo package/publish."""
    report = json.loads((cli_run["workspace"] / name).read_text(encoding="utf-8"))
    cargo_count = sum(
        1
        for label, _args, _env in preflight_recorder.records
        if label in _CARGO_BUILD_LABELS
    )
    assert set(report) == {"wrapper", "baseline", "final", "crates", "delta"}, (
        f"report schema drifted: {sorted(report)}"
    )
    assert report["wrapper"] == "sccache", f"wrapper recorded as {report['wrapper']!r}"
    assert len(report["crates"]) == cargo_count, (
        f"expected one record per cargo invocation ({cargo_count}), "
        f"got {len(report['crates'])}"
    )
    assert {record["subcommand"] for record in report["crates"]} == {
        "package",
        "publish",
    }, "records should cover both the package and the publish phase"
    assert all(
        record["requests"] == 10 and record["hits"] == 8 and record["misses"] == 2
        for record in report["crates"]
    ), f"each record should carry the stub's per-query delta: {report['crates']}"
    assert report["delta"]["requests"] == 10 * cargo_count, (
        f"pipeline delta should sum the per-invocation deltas: {report['delta']}"
    )
