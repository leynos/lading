"""Unit tests for :mod:`lading.commands.publish_sccache` (issue #252).

Covers the session lifecycle (baseline, per-invocation record, finish), its
never-raise guarantee, the JSON report, and the factory's opt-in and
no-wrapper paths. The pipeline integration lives in
``test_sccache_dispatch``.
"""

import collections.abc as cabc
import json
import logging
import typing as typ
from pathlib import Path

import pytest

from lading.commands import (
    publish_pipeline,
    publish_sccache,
    publish_sccache_report,
)
from lading.utils import metrics
from tests.helpers.path_normalization import normalized

from .sccache_doubles import (
    JSON_QUERY,
    PIPELINE_LOGGER,
    WRAPPER,
    ScriptedRunner,
    payload,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_WRITE_FAILURE_MESSAGE = "write refused"


@pytest.fixture(autouse=True)
def _metrics_registry() -> cabc.Iterator[None]:
    """Reset the in-process metrics registry around each test."""
    metrics.reset()
    yield
    metrics.reset()


def _session(
    runner: ScriptedRunner, tmp_path: Path, *, json_path: Path | None = None
) -> publish_sccache.SccacheSession:
    """Build a session against the standard wrapper rooted at ``tmp_path``."""
    return publish_sccache.SccacheSession(
        wrapper=WRAPPER, runner=runner, cwd=tmp_path, json_path=json_path
    )


def _run_two_invocation_session(
    tmp_path: Path, report: Path | None
) -> publish_sccache.SccacheSession:
    """Run baseline, one package, one publish, and finish against a script."""
    runner = ScriptedRunner([
        payload(100, 90, 10),
        payload(140, 128, 12),
        payload(150, 130, 20, errors=1),
    ])
    session = _session(runner, tmp_path, json_path=report)
    session.begin()
    session.record("alpha", "package", 84.25)
    session.record("alpha", "publish", 61.9)
    session.finish()
    return session


def test_session_attributes_deltas_per_invocation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Each record differences against the previous snapshot and logs a line."""
    caplog.set_level(logging.INFO, logger=PIPELINE_LOGGER)

    session = _run_two_invocation_session(tmp_path, tmp_path / "sccache.json")

    assert session.records == snapshot(name="records"), (
        "each invocation must be recorded with its differenced statistics"
    )
    messages = normalized(str(caplog.messages), tmp_path)
    assert normalized(messages, WRAPPER, placeholder="<wrapper>") == snapshot(
        name="messages"
    ), "the session must log each record and the cumulative summary"
    # Three JSON snapshots plus the human-readable mirror.
    assert (
        metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="success") == 4
    ), "three JSON queries and the text mirror must count as four successes"
    assert (
        metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 0
    ), "a session in which every query succeeds must record no failures"


def test_session_writes_report_atomically(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """The report carries raw payloads, per-invocation records, and the delta."""
    report = tmp_path / "reports" / "sccache.json"

    _run_two_invocation_session(tmp_path, report)

    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["wrapper"] == str(WRAPPER), (
        "the report must name the wrapper the statistics came from"
    )
    assert written["baseline"]["stats"]["compile_requests"] == 100, (
        "the report must carry the baseline query's raw payload"
    )
    assert written["final"]["stats"]["compile_requests"] == 150, (
        "the report must carry the final query's raw payload"
    )
    assert written["delta"] == snapshot(name="delta"), (
        "the report delta must match the committed snapshot"
    )
    assert written["crates"] == snapshot(name="crates"), (
        "each invocation must contribute a per-crate record"
    )
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
    assert written["delta"]["requests"] == 50, (
        "the delta must difference the final query against the baseline"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sccache.json"], (
        "no temporary file may survive a successful replacement"
    )


def test_report_replacement_failure_keeps_the_existing_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the rename fails the old report is untouched and the temp removed."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
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
    assert len(caplog.messages) == 1, (
        "a failed replacement must log exactly one warning"
    )
    assert caplog.messages[0].startswith(
        f"Could not write compiler cache report to {report}: "
    ), "the warning must name the report path that could not be replaced"


def test_atomic_write_removes_temporary_file_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write failure removes the temporary report file before propagating."""
    temporary = tmp_path / ".sccache.json.write-failure"

    class _FailingStream:
        """Temporary stream double that creates a file then rejects writes."""

        name = str(temporary)

        def __enter__(self) -> _FailingStream:
            temporary.touch()
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, _content: str) -> None:
            raise OSError(_WRITE_FAILURE_MESSAGE)

    monkeypatch.setattr(
        publish_sccache_report.tempfile,
        "NamedTemporaryFile",
        lambda *_args, **_kwargs: _FailingStream(),
    )

    with pytest.raises(OSError, match=_WRITE_FAILURE_MESSAGE):
        publish_sccache_report.write_atomically(tmp_path / "sccache.json", "report")

    assert not temporary.exists(), "a failed write must remove its temporary file"


def test_failed_baseline_disables_session_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A wrapper that cannot answer at baseline switches the session off."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
    runner = ScriptedRunner([payload(1, 1, 0)], failing_query_index=0)
    session = _session(runner, tmp_path, json_path=tmp_path / "report.json")

    session.begin()
    session.record("alpha", "package", 1.0)
    session.finish()

    assert not session.enabled, "a failed baseline query must disable the session"
    assert not session.records, "a session disabled at baseline must hold no records"
    assert runner.calls == [JSON_QUERY], "no further queries after the failure"
    assert caplog.messages == [
        (
            "Compiler cache statistics unavailable (baseline); disabling: "
            f"{WRAPPER} --show-stats exited 2: sccache: error: server gone"
        )
    ], "the baseline failure must log a warning naming the wrapper and the error"
    assert not (tmp_path / "report.json").exists(), (
        "a session that never enabled must not write a report"
    )
    assert (
        metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 1
    ), "a failed baseline query must increment the failure counter once"


def test_failed_query_mid_run_disables_further_queries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failure after some records keeps those records and stops querying."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
    runner = ScriptedRunner(
        [payload(10, 5, 5), payload(20, 15, 5)], failing_query_index=2
    )
    session = _session(runner, tmp_path)

    session.begin()
    session.record("alpha", "package", 1.0)
    session.record("beta", "package", 2.0)
    session.record("gamma", "package", 3.0)
    session.finish()

    assert [record.crate for record in session.records] == ["alpha"], (
        "the records taken before the failure must be retained"
    )
    assert runner.calls == [JSON_QUERY, JSON_QUERY, JSON_QUERY], (
        "querying must stop after the mid-run failure"
    )
    assert len(caplog.messages) == 1, "a mid-run failure must log exactly one warning"
    assert "cargo package beta" in caplog.messages[0], (
        "the warning must name the invocation whose query failed"
    )


def test_text_query_failure_still_writes_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The human-readable mirror is best-effort; the JSON report still lands."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
    runner = ScriptedRunner([payload(1, 1, 0)], text_exit_code=1)
    report = tmp_path / "report.json"
    session = _session(runner, tmp_path, json_path=report)

    session.begin()
    session.finish()

    assert report.exists(), (
        "the JSON report must still be written when the text mirror fails"
    )
    assert caplog.messages == [
        (
            "Compiler cache statistics text unavailable: "
            f"{WRAPPER} --show-stats exited 1: Compile requests   9\n"
            "Cache location    ghac"
        )
    ], "the text failure must log a warning naming the wrapper and the error"
    assert (
        metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="failure") == 1
    ), "the failed text query must count once as a failure"
    assert (
        metrics.counter_value(publish_sccache.QUERY_METRIC, outcome="success") == 1
    ), "the successful JSON query must count once as a success"


def test_unwritable_report_path_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A report path under a file cannot be created; that is a WARNING only."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    runner = ScriptedRunner([payload(1, 1, 0)])
    session = _session(runner, tmp_path, json_path=blocker / "report.json")

    session.begin()
    session.finish()

    assert len(caplog.messages) == 1, (
        "an unwritable report path must log exactly one warning"
    )
    assert caplog.messages[0].startswith(
        f"Could not write compiler cache report to {blocker / 'report.json'}: "
    ), "the warning must name the unwritable report path"


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
    options = publish_pipeline._PublishExecutionOptions(
        live=False,
        allow_dirty=True,
        sccache_stats=sccache_stats,
        sccache_stats_json=sccache_stats_json,
    )

    session = publish_sccache.create_session(
        options,
        runner=ScriptedRunner([]),
        workspace_root=tmp_path,
        env={"RUSTC_WRAPPER": str(WRAPPER)},
    )

    assert (session is not None) is expects_session, (
        "a report path must imply an enabled session and a lone flag must not"
    )
    if session is not None and sccache_stats_json is not None:
        assert session.json_path == tmp_path / sccache_stats_json, (
            "the report path is resolved against the workspace root"
        )


def test_create_session_warns_without_wrapper(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requesting statistics without an sccache wrapper skips with a WARNING."""
    caplog.set_level(logging.WARNING, logger=PIPELINE_LOGGER)
    options = publish_pipeline._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True
    )

    session = publish_sccache.create_session(
        options, runner=ScriptedRunner([]), workspace_root=tmp_path, env={}
    )

    assert session is None, (
        "a request for statistics without an sccache wrapper must create no session"
    )
    assert caplog.messages == [
        (
            "Compiler cache statistics requested but RUSTC_WRAPPER does not name "
            "an sccache binary; skipping"
        )
    ], "a skipped session must warn that RUSTC_WRAPPER is not sccache"


def test_create_session_binds_wrapper_root_and_report(tmp_path: Path) -> None:
    """An enabled session carries the wrapper, workspace root, and JSON path."""
    report = tmp_path / "report.json"
    options = publish_pipeline._PublishExecutionOptions(
        live=False, allow_dirty=True, sccache_stats=True, sccache_stats_json=report
    )

    session = publish_sccache.create_session(
        options,
        runner=ScriptedRunner([]),
        workspace_root=tmp_path,
        env={"RUSTC_WRAPPER": str(WRAPPER)},
    )

    assert session is not None, (
        "a wrapper plus a statistics request must create a session"
    )
    assert session.enabled, "a session created with a valid wrapper must be enabled"
    assert (session.wrapper, session.cwd, session.json_path) == (
        WRAPPER,
        tmp_path,
        report,
    ), "an absolute report path is kept as given"


def test_create_session_resolves_a_relative_report_path(tmp_path: Path) -> None:
    """A relative report path follows the workspace root, not the process cwd."""
    options = publish_pipeline._PublishExecutionOptions(
        live=False,
        allow_dirty=True,
        sccache_stats_json=Path("target") / "sccache.json",
    )

    session = publish_sccache.create_session(
        options,
        runner=ScriptedRunner([]),
        workspace_root=tmp_path,
        env={"RUSTC_WRAPPER": str(WRAPPER)},
    )

    assert session is not None, "a relative report path must still create a session"
    assert session.json_path == tmp_path / "target" / "sccache.json", (
        "relative report paths must be resolved against the workspace root"
    )
