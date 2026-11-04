"""Record Cyclopts result and stream behaviour for one interpreter.

Run with the interpreter under test. Emits one JSON document on stdout so the
two version arms can be diffed mechanically. ANSI escapes survive ``repr`` and
are visible as ``\\x1b``.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import cyclopts

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "cli_fixture.py"

# Rich reads these when a console is constructed. ``FORCE_COLOR`` is removed
# rather than set to zero: Rich treats its presence as a request for colour.
CONTROLS = {
    "COLUMNS": "40",
    "LINES": "24",
    "TERM": "dumb",
    "NO_COLOR": "1",
}

CASES: list[tuple[str, list[str]]] = [
    ("summary", ["summary"]),
    ("none", ["nothing"]),
    ("int-nonzero", ["status"]),
    ("bool-true", ["yes"]),
    ("bool-false", ["no"]),
    ("systemexit-str", ["abort"]),
    ("invalid-value", ["typed", "not-an-int"]),
    ("unknown-command", ["unknown"]),
    ("help", ["--help"]),
    ("missing-subcommand", []),
]


def _probe_in_process(result_action: str | None, argv: list[str]) -> dict[str, Any]:
    """Call the app in process and record what the caller observes."""
    from cli_fixture import build_app

    record: dict[str, Any] = {}
    try:
        app = build_app(result_action)
    except TypeError as err:
        return {"construction_error": f"TypeError: {err}"}

    out, err_buf = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err_buf):
            value = app(argv)
        record["outcome"] = {"returned": _describe(value)}
    except SystemExit as exc:
        record["outcome"] = {"system_exit": _describe(exc.code)}
    except BaseException as exc:  # noqa: BLE001 - the comparison records failures
        record["outcome"] = {"raised": f"{type(exc).__name__}: {exc}"}
    record["stdout"] = out.getvalue()
    record["stderr"] = err_buf.getvalue()
    return record


def _describe(value: Any) -> dict[str, str]:
    """Render one outcome so a JSON diff is unambiguous."""
    return {"type": type(value).__name__, "repr": repr(value)}


def _probe_subprocess(
    mode: str, result_action: str | None, argv: list[str]
) -> dict[str, Any]:
    """Run the fixture as a separate process and record its streams."""
    command = [sys.executable, str(FIXTURE), "--mode", mode]
    if result_action is not None:
        command += ["--result-action", result_action]
    env = {**os.environ, "COLUMNS": "40", "LINES": "24"}
    completed = subprocess.run(
        [*command, *argv],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=HERE,
        timeout=60,
    )
    return {
        "status": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> None:
    """Emit the comparison record for this interpreter."""
    os.environ.update(CONTROLS)
    os.environ.pop("FORCE_COLOR", None)
    document: dict[str, Any] = {
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "cyclopts": cyclopts.__version__,
        "controls": CONTROLS,
        "cases": {},
    }
    for name, argv in CASES:
        document["cases"][name] = {
            "argv": argv,
            "in_process_default": _probe_in_process(None, argv),
            "in_process_return_value": _probe_in_process("return_value", argv),
            "process_default": _probe_subprocess("default", None, argv),
            "process_dispatcher": _probe_subprocess("dispatcher", None, argv),
            "process_return_value_dispatcher": _probe_subprocess(
                "dispatcher", "return_value", argv
            ),
        }
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()