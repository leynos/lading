"""Test doubles shared by the sccache session and dispatch tests (issue #252).

``ScriptedRunner`` answers sccache queries from a scripted list of payloads
and every cargo command with success, recording each call in order, so a
test can assert the exact query sequence around the pipeline.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
from pathlib import Path

WRAPPER = Path("/opt/ci-tools/sccache")
JSON_QUERY = (str(WRAPPER), "--show-stats", "--stats-format=json")
TEXT_QUERY = (str(WRAPPER), "--show-stats")
TEXT_OUTPUT = "Compile requests   9\nCache location    ghac\n"
PIPELINE_LOGGER = "lading.commands.publish_sccache"


def payload(requests: int, hits: int, misses: int, errors: int = 0) -> str:
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
class ScriptedRunner:
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
        """Record ``command`` and answer it from the script."""
        del cwd, env, echo_stdout
        command_tuple = tuple(command)
        self.calls.append(command_tuple)
        if command_tuple == JSON_QUERY:
            index = self._json_queries
            self._json_queries += 1
            if index == self.failing_query_index:
                return 2, "", "sccache: error: server gone"
            payload = self.json_payloads[min(index, len(self.json_payloads) - 1)]
            return 0, payload, ""
        if command_tuple == TEXT_QUERY:
            return self.text_exit_code, TEXT_OUTPUT, ""
        return 0, "", ""


__all__ = [
    "JSON_QUERY",
    "PIPELINE_LOGGER",
    "TEXT_OUTPUT",
    "TEXT_QUERY",
    "WRAPPER",
    "ScriptedRunner",
    "payload",
]
