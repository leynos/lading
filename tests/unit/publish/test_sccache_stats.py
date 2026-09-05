"""Unit tests for :mod:`lading.commands.publish_sccache_stats` (issue #252).

Covers wrapper detection from ``RUSTC_WRAPPER``, counter extraction from real
sccache 0.12, 0.14, and 0.17 payload shapes, the query adapters through a recording
runner, and one query through the production subprocess runner against a stub
``sccache`` script on ``PATH``.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from lading.commands import publish_sccache_stats as stats
from lading.runtime import CommandSpawnError, subprocess_runner

_DATA_DIR = Path(__file__).parent / "data"


def _payload(version: str) -> dict[str, object]:
    """Load the recorded sccache payload for ``version``."""
    path = _DATA_DIR / f"sccache_stats_{version.replace('.', '_')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        pytest.param(
            {"RUSTC_WRAPPER": "/opt/ci-tools/sccache"},
            Path("/opt/ci-tools/sccache"),
            id="absolute-sccache",
        ),
        pytest.param({"RUSTC_WRAPPER": "sccache"}, Path("sccache"), id="bare-sccache"),
        pytest.param(
            {"RUSTC_WRAPPER": r"C:\tools\sccache.exe"},
            Path(r"C:\tools\sccache.exe"),
            id="windows-exe",
        ),
        pytest.param(
            {"RUSTC_WRAPPER": r"C:\tools\SCCACHE.EXE"},
            Path(r"C:\tools\SCCACHE.EXE"),
            id="windows-upper-case",
        ),
        pytest.param(
            {"RUSTC_WRAPPER": "  /opt/ci-tools/sccache  "},
            Path("/opt/ci-tools/sccache"),
            id="surrounding-whitespace",
        ),
        pytest.param({"RUSTC_WRAPPER": "/usr/bin/ccache"}, None, id="ccache"),
        pytest.param({"RUSTC_WRAPPER": "/opt/bin/my-sccache"}, None, id="prefixed"),
        pytest.param({"RUSTC_WRAPPER": ""}, None, id="empty"),
        pytest.param({}, None, id="unset"),
    ],
)
def test_detect_wrapper(env: dict[str, str], expected: Path | None) -> None:
    """Only a wrapper whose basename starts with ``sccache`` is detected."""
    assert stats.detect_wrapper(env) == expected, (
        f"RUSTC_WRAPPER={env.get('RUSTC_WRAPPER')!r} should resolve to {expected!r}"
    )


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        pytest.param(
            "0.12",
            stats.SccacheCounters(requests=412, hits=398, misses=14, errors=0),
            id="sccache-0.12",
        ),
        pytest.param(
            "0.14",
            stats.SccacheCounters(requests=11128, hits=6738, misses=1233, errors=10),
            id="sccache-0.14",
        ),
        pytest.param(
            "0.17",
            stats.SccacheCounters(requests=2044, hits=1690, misses=27, errors=1),
            id="sccache-0.17",
        ),
    ],
)
def test_parse_counters_reads_recorded_payloads(
    version: str, expected: stats.SccacheCounters
) -> None:
    """Hits and misses sum across languages; errors sum every error counter."""
    assert stats.parse_counters(_payload(version)) == expected, (
        f"the recorded sccache {version} payload should reduce to {expected}"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({}, stats.SccacheCounters(), id="no-stats"),
        pytest.param({"stats": "nope"}, stats.SccacheCounters(), id="stats-not-object"),
        pytest.param(
            {"stats": {"compile_requests": 5}},
            stats.SccacheCounters(requests=5),
            id="only-requests",
        ),
        pytest.param(
            {
                "stats": {
                    "compile_requests": "5",
                    "cache_hits": {"counts": {"Rust": "x"}},
                }
            },
            stats.SccacheCounters(),
            id="non-integer-values",
        ),
        pytest.param(
            {"stats": {"cache_hits": {"counts": [1, 2]}, "cache_misses": 7}},
            stats.SccacheCounters(),
            id="malformed-sections",
        ),
    ],
)
def test_parse_counters_tolerates_missing_or_malformed_keys(
    payload: dict[str, object], expected: stats.SccacheCounters
) -> None:
    """Gaps in the payload count as zero rather than failing the run."""
    assert stats.parse_counters(payload) == expected, (
        "missing or malformed keys must count as zero, not fail"
    )


def test_counters_subtraction_is_field_wise() -> None:
    """Differencing two snapshots yields the counters between them."""
    later = stats.SccacheCounters(requests=10, hits=7, misses=2, errors=1)
    earlier = stats.SccacheCounters(requests=4, hits=3, misses=1, errors=0)

    assert later - earlier == stats.SccacheCounters(
        requests=6, hits=4, misses=1, errors=1
    ), "subtraction must be field-wise"
    assert (later - earlier).as_dict() == {
        "requests": 6,
        "hits": 4,
        "misses": 1,
        "errors": 1,
    }, "as_dict() must expose the four counters by name"


@dc.dataclass
class _RecordingRunner:
    """Runner double recording each call and replaying a scripted result."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    calls: list[tuple[tuple[str, ...], Path | None, bool]] = dc.field(
        default_factory=list
    )

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del env
        self.calls.append((tuple(command), cwd, echo_stdout))
        return self.exit_code, self.stdout, self.stderr


def test_query_snapshot_runs_json_query_without_echo(tmp_path: Path) -> None:
    """The JSON query names the wrapper, asks for JSON, and is never mirrored."""
    runner = _RecordingRunner(stdout=json.dumps(_payload("0.12")))
    wrapper = Path("/opt/ci-tools/sccache")

    snapshot = stats.query_snapshot(wrapper, runner=runner, cwd=tmp_path)

    assert runner.calls == [
        (
            ("/opt/ci-tools/sccache", "--show-stats", "--stats-format=json"),
            tmp_path,
            False,
        )
    ]
    assert snapshot.counters == stats.SccacheCounters(
        requests=412, hits=398, misses=14, errors=0
    ), "the snapshot must carry the parsed counters"
    assert snapshot.raw["version"] == "0.12.0", "the raw payload must be kept"


def test_query_text_returns_human_readable_output(tmp_path: Path) -> None:
    """The text query is the plain ``--show-stats`` form."""
    runner = _RecordingRunner(stdout="Compile requests  412\n")

    text = stats.query_text(Path("sccache"), runner=runner, cwd=tmp_path)

    assert text == "Compile requests  412\n", "stdout is returned verbatim"
    assert runner.calls == [(("sccache", "--show-stats"), tmp_path, False)], (
        "the text query must be the plain --show-stats form, never echoed"
    )


def test_query_snapshot_reports_non_zero_exit(tmp_path: Path) -> None:
    """A failing sccache is reported with its status and stderr detail."""
    runner = _RecordingRunner(exit_code=2, stderr="sccache: error: no server\n")

    with pytest.raises(stats.SccacheQueryError) as excinfo:
        stats.query_snapshot(Path("sccache"), runner=runner, cwd=tmp_path)

    assert excinfo.value.exit_code == 2, "the exit status must be preserved"
    assert str(excinfo.value) == (
        "sccache --show-stats exited 2: sccache: error: no server"
    )


def test_query_snapshot_wraps_spawn_failures(tmp_path: Path) -> None:
    """A wrapper that cannot be spawned is reported as a query error."""

    def _runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, cwd, env, echo_stdout
        program = "sccache"
        raise CommandSpawnError(program, FileNotFoundError(program))

    with pytest.raises(stats.SccacheQueryError) as excinfo:
        stats.query_snapshot(Path("sccache"), runner=_runner, cwd=tmp_path)

    assert excinfo.value.exit_code is None, "a spawn failure has no exit status"
    assert "could not be run" in str(excinfo.value), "the message must say why"


@pytest.mark.parametrize(
    ("stdout", "reason", "expected_fragment"),
    [
        pytest.param("not json", "invalid-json", "invalid JSON output", id="invalid"),
        pytest.param("[1, 2]", "non-object", "non-object JSON payload", id="array"),
    ],
)
def test_query_snapshot_rejects_unparseable_output(
    tmp_path: Path, stdout: str, reason: str, expected_fragment: str
) -> None:
    """Output that is not a JSON object is a structured parse error."""
    runner = _RecordingRunner(stdout=stdout)

    with pytest.raises(stats.SccacheStatsParseError, match=expected_fragment) as info:
        stats.query_snapshot(Path("sccache"), runner=runner, cwd=tmp_path)

    assert (info.value.wrapper, info.value.reason) == (Path("sccache"), reason), (
        "the parse error must carry the wrapper and a machine-readable reason"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stub script")
def test_query_snapshot_runs_stub_sccache_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production runner resolves a bare wrapper name through ``PATH``.

    A stub ``sccache`` script records its arguments and answers with the
    recorded 0.14 payload, so this exercises the real subprocess path that CI
    takes when ``RUSTC_WRAPPER`` is a bare name.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload_path = _DATA_DIR / "sccache_stats_0_14.json"
    argument_log = tmp_path / "sccache-args.log"
    stub = bin_dir / "sccache"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{argument_log}"\ncat "{payload_path}"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    snapshot = stats.query_snapshot(
        Path("sccache"), runner=subprocess_runner, cwd=tmp_path
    )

    assert argument_log.read_text(encoding="utf-8") == (
        "--show-stats --stats-format=json\n"
    )
    assert snapshot.counters.hits == 6738, "the stub's payload must be parsed"
