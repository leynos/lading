"""Unit tests for :mod:`lading.commands.publish_sccache` (issue #252).

Covers the session lifecycle (baseline, per-invocation record, finish), its
never-raise guarantee, the JSON report, the factory's opt-in and no-wrapper
paths, and the pipeline integration: with ``sccache_stats`` on, the dispatch
layer queries the wrapper before the first cargo build and after every
per-crate cargo invocation, in both the dry-run and live pipelines.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import logging
from pathlib import Path

import pytest

from lading.commands import publish, publish_sccache
from lading.commands.publish_sccache_stats import SccacheCounters
from lading.utils import metrics

_WRAPPER = Path("/opt/ci-tools/sccache")
_JSON_QUERY = (str(_WRAPPER), "--show-stats", "--stats-format=json")
_TEXT_QUERY = (str(_WRAPPER), "--show-stats")
_TEXT_OUTPUT = "Compile requests   9\nCache location    ghac\n"
_PIPELINE_LOGGER = "lading.commands.publish_sccache"


def _payload(requests: int, hits: int, misses: int, errors: int = 0) -> str:
    """Return a JSON payload carrying the given counters."""
    return json.dumps({
        "stats": {
            "compile_requests": requests,
            "cache_hits": {"counts": {"Rust": hits}},
            "cache_misses": {"counts": {"Rust": misses}},
            "cache_read_errors": errors,
        },
        "version": "0.14.0",
    })


@dc.dataclass
class _ScriptedRunner:
    """Runner double answering sccache queries from a script and cargo with 0.

    ``json_payloads`` are served in order to JSON queries; once exhausted the
    last payload repeats. ``failing_query_index`` makes the query with that
    ordinal exit non-zero, so a mid-run failure can be staged.
    """

    json_payloads: list[str]
    failing_query_index: int | None = None
    text_exit_code: int = 0
    calls: list[tuple[str, ...]] = dc.field(default_factory=list)
    _json_queries: int = 0

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del cwd, env, echo_stdout
        command_tuple = tuple(command)
        self.calls.append(command_tuple)
        if command_tuple == _JSON_QUERY:
            index = self._json_queries
            self._json_queries += 1
            if index == self.failing_query_index:
                return 2, "", "sccache: error: server gone"
            payload = self.json_payloads[min(index, len(self.json_payloads) - 1)]
            return 0, payload, ""
        if command_tuple == _TEXT_QUERY:
            return self.text_exit_code, _TEXT_OUTPUT, ""
        return 0, "", ""


@pytest.fixture(autouse=True)
def _metrics_registry() -> cabc.Iterator[None]:
    """Reset the in-process metrics registry around each test."""
    metrics.reset()
    yield
    metrics.reset()


def _session(
    runner: _ScriptedRunner, tmp_path: Path, *, json_path: Path | None = None
) -> publish_sccache.SccacheSession:
    return publish_sccache.SccacheSession(
        wrapper=_WRAPPER, runner=runner, cwd=tmp_path, json_path=json_path
    )


def _run_two_invocation_session(
    tmp_path: Path, report: Path | None
) -> publish_sccache.SccacheSession:
    """Run baseline, one package, one publish, and finish against a script."""
    runner = _ScriptedRunner([
        _payload(100, 90, 10),
        _payload(140, 128, 12),
        _payload(150, 130, 20, errors=1),
    ])
    session = _session(runner, tmp_path, json_path=report)
    session.begin()
    session.record("alpha", "package", 84.25)
    session.record("alpha", "publish", 61.9)
    session.finish()
    return session


def test_session_attributes_deltas_per_invocation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Each record differences against the previous snapshot and logs a line."""
    caplog.set_level(logging.INFO, logger=_PIPELINE_LOGGER)

    session = _run_two_invocation_session(tmp_path, tmp_path / "sccache.json")

    assert session.records == (
        publish_sccache.SccacheCrateRecord(
            "alpha", "package", 84.25, SccacheCounters(40, 38, 2, 0)
        ),
        publish_sccache.SccacheCrateRecord(
            "alpha", "publish", 61.9, SccacheCounters(10, 2, 8, 1)
        ),
    )
    assert caplog.messages == [
        f"Compiler cache statistics enabled via {_WRAPPER}",
        (
            "Compiler cache for cargo package alpha: 84.2s, "
            "requests=40 hits=38 misses=2 errors=0"
        ),
        (
            "Compiler cache for cargo publish alpha: 61.9s, "
            "requests=10 hits=2 misses=8 errors=1"
        ),
        (
            "Compiler cache over the publish pipeline: "
            "requests=50 hits=40 misses=10 errors=1"
        ),
        (
            "sccache statistics (cumulative for the server's lifetime):\n"
            "Compile requests   9\nCache location    ghac"
        ),
        f"Compiler cache report written to {tmp_path / 'sccache.json'}",
    ]
    # Three JSON snapshots plus the human-readable mirror.
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="success") == 4
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 0


def test_session_writes_report_atomically(tmp_path: Path) -> None:
    """The report carries raw payloads, per-invocation records, and the delta."""
    report = tmp_path / "reports" / "sccache.json"

    _run_two_invocation_session(tmp_path, report)

    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["wrapper"] == str(_WRAPPER)
    assert written["baseline"]["stats"]["compile_requests"] == 100
    assert written["final"]["stats"]["compile_requests"] == 150
    assert written["delta"] == {"requests": 50, "hits": 40, "misses": 10, "errors": 1}
    assert written["crates"] == [
        {
            "crate": "alpha",
            "subcommand": "package",
            "seconds": 84.25,
            "requests": 40,
            "hits": 38,
            "misses": 2,
            "errors": 0,
        },
        {
            "crate": "alpha",
            "subcommand": "publish",
            "seconds": 61.9,
            "requests": 10,
            "hits": 2,
            "misses": 8,
            "errors": 1,
        },
    ]
    assert [path.name for path in report.parent.iterdir()] == [report.name], (
        "the atomic write must leave no temporary file behind"
    )


def test_report_replaces_an_existing_file_atomically(tmp_path: Path) -> None:
    """A second run replaces the previous report and leaves no temporary file."""
    report = tmp_path / "sccache.json"
    report.write_text('{"stale": true}', encoding="utf-8")

    _run_two_invocation_session(tmp_path, report)

    written = json.loads(report.read_text(encoding="utf-8"))
    assert "stale" not in written, "the previous report must be replaced"
    assert written["delta"]["requests"] == 50
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sccache.json"], (
        "no temporary file may survive a successful replacement"
    )


def test_report_replacement_failure_keeps_the_existing_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the rename fails the old report is untouched and the temp removed."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    report = tmp_path / "sccache.json"
    report.write_text('{"previous": true}', encoding="utf-8")

    def _refuse_replace(self: Path, target: Path) -> Path:
        message = f"refusing to replace {target}"
        raise PermissionError(message)

    monkeypatch.setattr(Path, "replace", _refuse_replace)

    _run_two_invocation_session(tmp_path, report)

    assert json.loads(report.read_text(encoding="utf-8")) == {"previous": True}, (
        "a failed replacement must leave the existing report intact"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sccache.json"], (
        "the temporary file must be removed after a failed replacement"
    )
    assert len(caplog.messages) == 1
    assert caplog.messages[0].startswith(
        f"Could not write compiler cache report to {report}: "
    )


def test_failed_baseline_disables_session_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A wrapper that cannot answer at baseline switches the session off."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    runner = _ScriptedRunner([_payload(1, 1, 0)], failing_query_index=0)
    session = _session(runner, tmp_path, json_path=tmp_path / "report.json")

    session.begin()
    session.record("alpha", "package", 1.0)
    session.finish()

    assert not session.enabled
    assert session.records == ()
    assert runner.calls == [_JSON_QUERY], "no further queries after the failure"
    assert caplog.messages == [
        (
            "Compiler cache statistics unavailable (baseline); disabling: "
            f"{_WRAPPER} --show-stats exited 2: sccache: error: server gone"
        )
    ]
    assert not (tmp_path / "report.json").exists()
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 1


def test_failed_query_mid_run_disables_further_queries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failure after some records keeps those records and stops querying."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    runner = _ScriptedRunner(
        [_payload(10, 5, 5), _payload(20, 15, 5)], failing_query_index=2
    )
    session = _session(runner, tmp_path)

    session.begin()
    session.record("alpha", "package", 1.0)
    session.record("beta", "package", 2.0)
    session.record("gamma", "package", 3.0)
    session.finish()

    assert [record.crate for record in session.records] == ["alpha"]
    assert runner.calls == [_JSON_QUERY, _JSON_QUERY, _JSON_QUERY]
    assert len(caplog.messages) == 1
    assert "cargo package beta" in caplog.messages[0]


def test_text_query_failure_still_writes_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The human-readable mirror is best-effort; the JSON report still lands."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    runner = _ScriptedRunner([_payload(1, 1, 0)], text_exit_code=1)
    report = tmp_path / "report.json"
    session = _session(runner, tmp_path, json_path=report)

    session.begin()
    session.finish()

    assert report.exists()
    assert caplog.messages == [
        (
            "Compiler cache statistics text unavailable: "
            f"{_WRAPPER} --show-stats exited 1: Compile requests   9\n"
            "Cache location    ghac"
        )
    ]
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 1
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="success") == 1


def test_unwritable_report_path_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A report path under a file cannot be created; that is a WARNING only."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    runner = _ScriptedRunner([_payload(1, 1, 0)])
    session = _session(runner, tmp_path, json_path=blocker / "report.json")

    session.begin()
    session.finish()

    assert len(caplog.messages) == 1
    assert caplog.messages[0].startswith(
        f"Could not write compiler cache report to {blocker / 'report.json'}: "
    )


@pytest.mark.parametrize(
    ("sccache_stats", "sccache_stats_json", "expects_session"),
    [
        pytest.param(False, None, False, id="neither"),
        pytest.param(True, None, True, id="flag-only"),
        pytest.param(False, Path("stats.json"), True, id="json-implies-flag"),
        pytest.param(True, Path("stats.json"), True, id="both"),
    ],
)
def test_create_session_treats_a_report_path_as_opting_in(
    tmp_path: Path,
    *,
    sccache_stats: bool,
    sccache_stats_json: Path | None,
    expects_session: bool,
) -> None:
    """A report path implies the measurement for library and CLI callers alike."""
    options = publish._PublishExecutionOptions(
        live=False,
        allow_dirty=True,
        sccache_stats=sccache_stats,
        sccache_stats_json=sccache_stats_json,
    )

    session = publish_sccache.create_session(
        options,
        runner=_ScriptedRunner([]),
        workspace_root=tmp_path,
        env={"RUSTC_WRAPPER": str(_WRAPPER)},
    )

    assert (session is not None) is expects_session
    if session is not None:
        assert session.json_path == sccache_stats_json


def test_create_session_warns_without_wrapper(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requesting statistics without an sccache wrapper skips with a WARNING."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    options = publish._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True
    )

    session = publish_sccache.create_session(
        options, runner=_ScriptedRunner([]), workspace_root=tmp_path, env={}
    )

    assert session is None
    assert caplog.messages == [
        (
            "Compiler cache statistics requested but RUSTC_WRAPPER does not name "
            "an sccache binary; skipping"
        )
    ]


def test_create_session_binds_wrapper_root_and_report(tmp_path: Path) -> None:
    """An enabled session carries the wrapper, workspace root, and JSON path."""
    report = tmp_path / "report.json"
    options = publish._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True, sccache_stats_json=report
    )

    session = publish_sccache.create_session(
        options,
        runner=_ScriptedRunner([]),
        workspace_root=tmp_path,
        env={"RUSTC_WRAPPER": str(_WRAPPER)},
    )

    assert session is not None
    assert session.enabled
    assert (session.wrapper, session.cwd, session.json_path) == (
        _WRAPPER,
        tmp_path,
        report,
    )


# --- pipeline integration -------------------------------------------------


def _cargo_calls(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [call for call in calls if call[0] == "cargo"]


def _label(call: tuple[str, ...]) -> str:
    """Name a recorded call as ``sccache:json``, ``sccache:text``, or cargo."""
    if call == _JSON_QUERY:
        return "sccache:json"
    if call == _TEXT_QUERY:
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
    runner: _ScriptedRunner
    report: Path


def _run_instrumented_dispatch(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    tmp_path: Path,
    *,
    live: bool,
) -> _DispatchRun:
    """Dispatch the pipeline with statistics on and a wrapper configured."""
    plan, preparation, _staging_root = publish_plan_and_prep
    payloads = [_payload(10 * step, 8 * step, 2 * step) for step in range(1, 9)]
    runner = _ScriptedRunner(payloads)
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
    monkeypatch.setenv("RUSTC_WRAPPER", str(_WRAPPER))

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
    caplog.set_level(logging.INFO, logger=_PIPELINE_LOGGER)
    monkeypatch.setenv("RUSTC_WRAPPER", str(_WRAPPER))

    run = _run_instrumented_dispatch(publish_plan_and_prep, tmp_path, live=False)

    cargo_calls = _cargo_calls(run.runner.calls)
    summary_lines = [
        message
        for message in caplog.messages
        if message.startswith("Compiler cache for")
    ]
    written = json.loads(run.report.read_text(encoding="utf-8"))
    assert len(summary_lines) == len(cargo_calls) == 2 * len(run.plan.publishable)
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
    assert written["delta"] == {"requests": 60, "hits": 48, "misses": 12, "errors": 0}


def test_dispatch_without_wrapper_warns_and_still_publishes(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing wrapper never blocks the dry run."""
    caplog.set_level(logging.WARNING, logger=_PIPELINE_LOGGER)
    monkeypatch.delenv("RUSTC_WRAPPER", raising=False)
    plan, preparation, _staging_root = publish_plan_and_prep
    runner = _ScriptedRunner([])
    options = publish._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True
    )

    publish._dispatch_publication(plan, preparation, options=options, runner=runner)

    assert all(call[0] == "cargo" for call in runner.calls)
    assert len(_cargo_calls(runner.calls)) == 2 * len(plan.publishable)
    assert len(caplog.messages) == 1
    assert "does not name an sccache binary" in caplog.messages[0]


def test_dispatch_reports_what_it_measured_when_a_crate_fails(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failing crate aborts the publish but the report still lands."""
    monkeypatch.setenv("RUSTC_WRAPPER", str(_WRAPPER))
    plan, preparation, _staging_root = publish_plan_and_prep
    runner = _ScriptedRunner([_payload(10, 8, 2), _payload(20, 16, 4)])
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
    assert runner.calls[-1] == _TEXT_QUERY


def test_dispatch_without_flag_never_queries(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag off a configured wrapper is left alone."""
    monkeypatch.setenv("RUSTC_WRAPPER", str(_WRAPPER))
    plan, preparation, _staging_root = publish_plan_and_prep
    runner = _ScriptedRunner([])

    publish._dispatch_publication(
        plan,
        preparation,
        options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
        runner=runner,
    )

    assert all(call[0] == "cargo" for call in runner.calls)
