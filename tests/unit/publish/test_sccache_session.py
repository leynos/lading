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


def test_session_attributes_deltas_per_invocation_and_reports(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Each record differences against the previous snapshot; finish reports."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish_sccache")
    runner = _ScriptedRunner([
        _payload(100, 90, 10),
        _payload(140, 128, 12),
        _payload(150, 130, 20, errors=1),
    ])
    report = tmp_path / "reports" / "sccache.json"
    session = _session(runner, tmp_path, json_path=report)

    session.begin()
    session.record("alpha", "package", 84.25)
    session.record("alpha", "publish", 61.9)
    session.finish()

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
        f"Compiler cache report written to {report}",
    ]
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
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="success") == 3
    assert metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 0


def test_failed_baseline_disables_session_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A wrapper that cannot answer at baseline switches the session off."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_sccache")
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
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_sccache")
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
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_sccache")
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


def test_unwritable_report_path_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A report path under a file cannot be created; that is a WARNING only."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_sccache")
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


def test_create_session_is_none_when_not_requested(tmp_path: Path) -> None:
    """Instrumentation is opt-in."""
    options = publish._PublishExecutionOptions(live=False, allow_dirty=True)

    session = publish_sccache.create_session(
        options,
        runner=_ScriptedRunner([]),
        workspace_root=tmp_path,
        env={"RUSTC_WRAPPER": str(_WRAPPER)},
    )

    assert session is None


def test_create_session_warns_without_wrapper(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requesting statistics without an sccache wrapper skips with a WARNING."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_sccache")
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


def _cargo_calls(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [call for call in calls if call[0] == "cargo"]


@pytest.mark.parametrize(
    ("live", "expected_sequence"),
    [
        pytest.param(
            False,
            [
                "sccache:json",
                "cargo:package",
                "sccache:json",
                "cargo:package",
                "sccache:json",
                "cargo:package",
                "sccache:json",
                "cargo:publish",
                "sccache:json",
                "cargo:publish",
                "sccache:json",
                "cargo:publish",
                "sccache:json",
                "sccache:text",
            ],
            id="dry-run",
        ),
        pytest.param(
            True,
            [
                "sccache:json",
                "cargo:package",
                "sccache:json",
                "cargo:publish",
                "sccache:json",
                "cargo:package",
                "sccache:json",
                "cargo:publish",
                "sccache:json",
                "cargo:package",
                "sccache:json",
                "cargo:publish",
                "sccache:json",
                "sccache:text",
            ],
            id="live",
        ),
    ],
)
def test_dispatch_brackets_every_cargo_invocation_with_a_query(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    *,
    live: bool,
    expected_sequence: list[str],
) -> None:
    """The baseline precedes the first build; a query follows each invocation."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish_sccache")
    monkeypatch.setenv("RUSTC_WRAPPER", str(_WRAPPER))
    plan, preparation, _staging_root = publish_plan_and_prep
    payloads = [_payload(10 * step, 8 * step, 2 * step) for step in range(1, 9)]
    runner = _ScriptedRunner(payloads)
    report = tmp_path / "sccache-report.json"
    options = publish._PublishExecutionOptions(
        live=live, allow_dirty=True, sccache_stats=True, sccache_stats_json=report
    )

    publish._dispatch_publication(plan, preparation, options=options, runner=runner)

    def _label(call: tuple[str, ...]) -> str:
        if call == _JSON_QUERY:
            return "sccache:json"
        if call == _TEXT_QUERY:
            return "sccache:text"
        return f"{call[0]}:{call[1]}"

    assert [_label(call) for call in runner.calls] == expected_sequence
    summary_lines = [
        message
        for message in caplog.messages
        if message.startswith("Compiler cache for")
    ]
    assert summary_lines == [
        f"Compiler cache for cargo {call[1]} {crate.name}: 0.0s, "
        "requests=10 hits=8 misses=2 errors=0"
        for call, crate in zip(
            _cargo_calls(runner.calls),
            [crate for crate in plan.publishable for _ in range(2)]
            if live
            else list(plan.publishable) * 2,
            strict=True,
        )
    ]
    written = json.loads(report.read_text(encoding="utf-8"))
    assert len(written["crates"]) == 2 * len(plan.publishable)
    assert written["delta"] == {"requests": 60, "hits": 48, "misses": 12, "errors": 0}


def test_dispatch_without_wrapper_warns_and_still_publishes(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing wrapper never blocks the dry run."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish_sccache")
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
