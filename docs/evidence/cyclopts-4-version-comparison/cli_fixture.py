"""A deterministic, minimal stand-in for lading's CLI result boundary.

The real CLI returns summary strings, maps ``int`` and ``bool`` outcomes to
exit statuses, and raises ``SystemExit`` for fatal errors. This fixture keeps
only those shapes so a Cyclopts version comparison measures the library, not
lading's workspace logic.
"""

from __future__ import annotations

import sys
from typing import Any

from cyclopts import App

SUMMARY = "Found 1 staging directory under " + "parser-summary-" * 6 + ", 64 B"

_UNSUPPORTED = object()


def build_app(result_action: str | None) -> App:
    """Build the fixture app, optionally pinning ``result_action``.

    Cyclopts 3.24 does not accept ``result_action``; the caller records that
    rejection rather than hiding it, because it is itself a comparison result.
    """
    kwargs = {} if result_action is None else {"result_action": result_action}
    app = App(**kwargs)

    @app.command
    def summary() -> str:
        """Return the long summary lading prints verbatim."""
        return SUMMARY

    @app.command
    def nothing() -> None:
        """Return ``None``, the success outcome that prints nothing."""

    @app.command
    def status() -> int:
        """Return a non-zero integer status."""
        return 3

    @app.command
    def yes() -> bool:
        """Return ``True``."""
        return True

    @app.command
    def no() -> bool:
        """Return ``False``."""
        return False

    @app.command
    def abort() -> None:
        """Raise ``SystemExit`` carrying a message, as lading bootstrap does."""
        raise SystemExit("fatal: workspace missing")

    @app.command
    def typed(count: int) -> str:
        """Require an integer so a parse failure is observable."""
        return f"count={count}"

    return app


def dispatch(app: App, argv: list[str]) -> int:
    """Print and map one result exactly as lading's dispatcher does."""
    try:
        result: Any = app(argv)
    except SystemExit as err:
        code = err.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    match result:
        case None:
            return 0
        case bool():
            return 0 if result else 1
        case int():
            return result
        case _:
            print(result)
            return 0


def main(argv: list[str]) -> int:
    """Entry point used by the process-level probes."""
    mode = "default"
    result_action: str | None = None
    if argv[:1] == ["--mode"]:
        mode = argv[1]
        argv = argv[2:]
    if argv[:1] == ["--result-action"]:
        result_action = argv[1]
        argv = argv[2:]
    try:
        app = build_app(result_action)
    except TypeError as err:
        print(f"unsupported: {err}", file=sys.stderr)
        return 64
    if mode == "dispatcher":
        return dispatch(app, argv)
    # The naive embedded caller ignores the return value, which is what a
    # process wrapper did before Cyclopts 4 changed the default action.
    app(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))