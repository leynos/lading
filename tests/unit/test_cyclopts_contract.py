"""Pin the Cyclopts behaviour lading's CLI boundary depends on.

Cyclopts 4 changed the default ``result_action`` to ``print_non_int_sys_exit``:
the app renders a command's return value through Rich and calls
:func:`sys.exit`. lading instead configures ``result_action="return_value"``
and lets :func:`lading.cli._dispatch_and_print` own printing and exit codes.

These tests use a throwaway app so they describe the upstream contract rather
than lading's own wiring, and one test asserts that lading's app is configured
to the mode the rest of the suite assumes. If a Cyclopts upgrade changes any of
this, the failure names the assumption that moved.
"""

import contextlib
import dataclasses as dc
import io

import pytest
from cyclopts import App

from lading import cli
from tests.helpers.cli_diagnostics import normalize_diagnostic


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """The observable result of calling an app with one argument list."""

    returned: object
    exit_code: object
    stdout: str
    stderr: str


def _invoke(app: App, argv: list[str]) -> _Invocation:
    """Call ``app`` and capture its return value, exit code, and streams."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    returned: object = None
    exit_code: object = None
    with (
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        try:
            returned = app(argv)
        except SystemExit as error:
            exit_code = error.code
    return _Invocation(returned, exit_code, stdout.getvalue(), stderr.getvalue())


def _make_app() -> App:
    """Return a throwaway app configured the way lading configures its own."""
    app = App(result_action="return_value")

    @app.command
    def echo(value: str) -> str:
        return f"echo {value}"

    @app.command
    def nothing() -> None:
        return None

    @app.command
    def count() -> int:
        return 3

    @app.command
    def fail() -> int:
        return 7

    return app


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(["echo", "hi"], "echo hi", id="string"),
        pytest.param(["nothing"], None, id="none"),
        pytest.param(["count"], 3, id="nonzero-int"),
        pytest.param(["fail"], 7, id="other-int"),
    ],
)
def test_return_value_mode_returns_raw_results(
    argv: list[str], expected: object
) -> None:
    """A successful command returns its value without printing or exiting."""
    result = _invoke(_make_app(), argv)

    assert result.exit_code is None, "a successful command must not raise SystemExit"
    assert result.returned == expected, "the raw command result must be returned"
    assert not result.stdout, "the app must not print a returned value"
    assert not result.stderr, "a successful command must not write to stderr"


def test_help_is_written_to_stdout() -> None:
    """Help is normal output, so it belongs on stdout."""
    result = _invoke(_make_app(), ["--help"])

    assert result.exit_code is None, "help must not raise SystemExit"
    assert "Usage" in result.stdout, "help must be written to stdout"
    assert not result.stderr, "help must not be written to stderr"


def test_unknown_command_reports_on_stderr() -> None:
    """An unknown command is a diagnostic, so it belongs on stderr."""
    result = _invoke(_make_app(), ["missing"])

    assert result.exit_code == 1, "an unknown command must exit non-zero"
    assert "Unknown command" in normalize_diagnostic(result.stderr), (
        "the diagnostic must be written to stderr"
    )
    assert not result.stdout, "a diagnostic must not be written to stdout"


def test_missing_required_argument_reports_on_stderr() -> None:
    """A missing required argument is a diagnostic, so it belongs on stderr."""
    result = _invoke(_make_app(), ["echo"])

    assert result.exit_code == 1, "a missing argument must exit non-zero"
    assert "requires an argument" in normalize_diagnostic(result.stderr), (
        "the diagnostic must be written to stderr"
    )
    assert not result.stdout, "a diagnostic must not be written to stdout"


def test_lading_app_returns_raw_results() -> None:
    """Lading's app must be configured for the boundary the dispatcher owns."""
    assert cli.app.result_action == ("return_value",), (
        "lading.cli.app must return raw results; _dispatch_and_print prints them"
    )
