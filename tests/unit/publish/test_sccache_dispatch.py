"""Pipeline integration tests for the sccache instrumentation (issue #252).

With ``sccache_stats`` on, ``_dispatch_publication`` queries the wrapper
before the first cargo build and after every per-crate cargo invocation, in
both the dry-run and live pipelines, logs one summary line per invocation,
writes the report even when a crate fails, and never queries when the flag is
off or no wrapper is configured.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import logging
from pathlib import Path

import pytest

from lading.commands import publish
from lading.utils import metrics

from .sccache_doubles import (
    JSON_QUERY,
    PIPELINE_LOGGER,
    TEXT_QUERY,
    WRAPPER,
    ScriptedRunner,
    payload,
)


@pytest.fixture(autouse=True)
def _metrics_registry() -> cabc.Iterator[None]:
    """Reset the in-process metrics registry around each test."""
    metrics.reset()
    yield
    metrics.reset()


def _cargo_calls(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Return only the cargo invocations from a recorded call list."""
    return [call for call in calls if call[0] == "cargo"]


def _label(call: tuple[str, ...]) -> str:
    """Name a recorded call as ``sccache:json``, ``sccache:text``, or cargo."""
    if call == JSON_QUERY:
        return "sccache:json"
    if call == TEXT_QUERY:
        return "sccache:text"
    return f"{call[0]}:{call[1]}"


def _expected_sequence(*, live: bool, crate_count: int) -> list[str]:
    """Return the query/cargo call order the dispatch layer must produce."""
    if live:
        per_crate = ["cargo:package", "sccache:json", "cargo:publish", "sccache:json"]
        builds = per_crate * crate_count
    else:
        builds = ["cargo:package", "sccache:json"] * crate_count + [
            "cargo:publish",
            "sccache:json",
        ] * crate_count
    return ["sccache:json", *builds, "sccache:text"]


@dc.dataclass(frozen=True, slots=True)
class _DispatchRun:
    """Outcome of one instrumented ``_dispatch_publication`` call."""

    plan: publish.PublishPlan
    runner: ScriptedRunner
    report: Path


def _run_instrumented_dispatch(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    tmp_path: Path,
    *,
    live: bool,
) -> _DispatchRun:
    """Dispatch the pipeline with statistics on and a wrapper configured."""
    plan, preparation, _staging_root = publish_plan_and_prep
    payloads = [payload(10 * step, 8 * step, 2 * step) for step in range(1, 9)]
    runner = ScriptedRunner(payloads)
    report = tmp_path / "sccache-report.json"
    options = publish._PublishExecutionOptions(
        live=live, allow_dirty=True, sccache_stats=True, sccache_stats_json=report
    )
    publish._dispatch_publication(plan, preparation, options=options, runner=runner)
    return _DispatchRun(plan=plan, runner=runner, report=report)


@pytest.mark.parametrize("live", [False, True], ids=["dry-run", "live"])
def test_dispatch_brackets_every_cargo_invocation_with_a_query(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    live: bool,
) -> None:
    """The baseline precedes the first build; a query follows each invocation."""
    monkeypatch.setenv("RUSTC_WRAPPER", str(WRAPPER))

    run = _run_instrumented_dispatch(publish_plan_and_prep, tmp_path, live=live)

    assert [_label(call) for call in run.runner.calls] == _expected_sequence(
        live=live, crate_count=len(run.plan.publishable)
    )


def test_dispatch_logs_one_summary_per_invocation_and_writes_report(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every cargo invocation gets a summary line and a record in the report.

    The dry-run pipeline is enough here: invocation order per mode is the
    ordering test's concern, and the summary and report logic is shared.
    """
    caplog.set_level(logging.INFO, logger=PIPELINE_LOGGER)
    monkeypatch.setenv("RUSTC_WRAPPER", str(WRAPPER))

    run = _run_instrumented_dispatch(publish_plan_and_prep, tmp_path, live=False)

    cargo_calls = _cargo_calls(run.runner.calls)
    summary_lines = [
        message
        for message in caplog.messages
        if message.startswith("Compiler cache for")
    ]
    written = json.loads(run.report.read_text(encoding="utf-8"))
    assert len(summary_lines) == len(cargo_calls) == 2 * len(run.plan.publishable), (
        "one summary line and one cargo call per crate phase"
    )
    assert all(
        line.endswith("0.0s, requests=10 hits=8 misses=2 errors=0")
        for line in summary_lines
    ), summary_lines
    assert [
        (record["subcommand"], record["crate"]) for record in written["crates"]
    ] == [
        (call[1], record["crate"])
        for call, record in zip(cargo_calls, written["crates"], strict=True)
    ]
    assert written["delta"] == {
        "requests": 60,
        "hits": 48,
        "misses": 12,
        "errors": 0,
    }, "the pipeline delta must sum the six per-invocation deltas"


def test_dispatch_without_wrapper_warns_and_still_publishes(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing wrapper never blocks the dry run."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
    monkeypatch.delenv("RUSTC_WRAPPER", raising=False)
    plan, preparation, _staging_root = publish_plan_and_prep
    runner = ScriptedRunner([])
    options = publish._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True
    )

    publish._dispatch_publication(plan, preparation, options=options, runner=runner)

    assert all(call[0] == "cargo" for call in runner.calls), (
        "no sccache query may run without a wrapper"
    )
    assert len(_cargo_calls(runner.calls)) == 2 * len(plan.publishable), (
        "the dry run must still package and publish every crate"
    )
    assert len(caplog.messages) == 1
    assert "does not name an sccache binary" in caplog.messages[0]


def test_dispatch_reports_what_it_measured_when_a_crate_fails(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failing crate aborts the publish but the report still lands."""
    monkeypatch.setenv("RUSTC_WRAPPER", str(WRAPPER))
    plan, preparation, _staging_root = publish_plan_and_prep
    runner = ScriptedRunner([payload(10, 8, 2), payload(20, 16, 4)])
    original_call = runner.__call__

    def _failing_second_package(
        command: cabc.Sequence[str], **kwargs: object
    ) -> tuple[int, str, str]:
        exit_code, stdout, stderr = original_call(command, **kwargs)
        is_package = tuple(command[:2]) == ("cargo", "package")
        if is_package and len(_cargo_calls(runner.calls)) == 2:
            return 1, "", "error: verify build failed"
        return exit_code, stdout, stderr

    report = tmp_path / "sccache-report.json"
    options = publish._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True, sccache_stats_json=report
    )

    with pytest.raises(publish.PublishPreflightError):
        publish._dispatch_publication(
            plan, preparation, options=options, runner=_failing_second_package
        )

    written = json.loads(report.read_text(encoding="utf-8"))
    assert [record["crate"] for record in written["crates"]] == ["alpha", "beta"]
    assert runner.calls[-1] == TEXT_QUERY


def test_dispatch_without_flag_never_queries(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag off a configured wrapper is left alone."""
    monkeypatch.setenv("RUSTC_WRAPPER", str(WRAPPER))
    plan, preparation, _staging_root = publish_plan_and_prep
    runner = ScriptedRunner([])

    publish._dispatch_publication(
        plan,
        preparation,
        options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
        runner=runner,
    )

    assert all(call[0] == "cargo" for call in runner.calls), (
        "a configured wrapper is left alone when the flag is off"
    )
