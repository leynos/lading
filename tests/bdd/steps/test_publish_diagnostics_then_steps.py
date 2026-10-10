"""Then-step definitions for publish diagnostics and progress reporting.

When a publish fails or is refused, the operator only has the subprocess's
output to work from. These steps assert on that output: the pre-flight failure
surface, the error and log lines it carries, and the per-crate progress lines
a successful run emits.
"""

import re

from pytest_bdd import parsers, then

from .cli_run_types import CliRunResult
from .test_publish_helpers import _assert_cli_run_succeeded, _split_names


@then("the command should not raise a preflight error about the flag")
def then_publish_flag_is_accepted(cli_run: CliRunResult) -> None:
    """Assert that the dry-run override flag does not fail pre-flight."""
    _assert_cli_run_succeeded(cli_run)
    assert (
        "--allow-unpublished-workspace-deps is only valid" not in cli_run["stderr"]
    ), (
        "the override flag must be accepted in dry-run mode without the "
        "dry-run-only rejection"
    )


@then("a PublishPreflightError should be raised")
def then_publish_preflight_error_is_reported(cli_run: CliRunResult) -> None:
    """Assert that the CLI surfaced a publish pre-flight failure."""
    assert cli_run["returncode"] == 1, (
        "a publish pre-flight failure must surface as exit status one"
    )


@then("no PublishPreflightError should be raised")
def then_publish_preflight_error_is_not_reported(cli_run: CliRunResult) -> None:
    """Assert that publish completed without a pre-flight failure."""
    _assert_cli_run_succeeded(cli_run)
    assert "PublishPreflightError" not in cli_run["stderr"], (
        f"publish should complete without a pre-flight failure:\n{cli_run['stderr']}"
    )


@then(parsers.parse('the error message should contain "{expected}"'))
def then_publish_error_message_contains(cli_run: CliRunResult, expected: str) -> None:
    """Assert that the CLI error output contains ``expected``."""
    assert expected in cli_run["stderr"], (
        "the reported error must contain the expected text"
    )


@then(parsers.parse('a WARNING log should be emitted containing "{expected}"'))
def then_publish_warning_log_contains(cli_run: CliRunResult, expected: str) -> None:
    """Assert that a warning log containing ``expected`` was emitted."""
    assert re.search(r"(?i)\bwarning\b", cli_run["stderr"]), (
        "Expected a WARNING-level log line in stderr"
    )
    assert expected in cli_run["stderr"], (
        f"Expected {expected!r} in stderr WARNING output"
    )


@then(parsers.parse('an INFO log should be emitted containing "{expected}"'))
def then_publish_info_log_contains(cli_run: CliRunResult, expected: str) -> None:
    """Assert that an INFO log line containing ``expected`` was emitted."""
    assert any(
        line.startswith("INFO: ") and expected in line
        for line in cli_run["stderr"].splitlines()
    ), f"Expected an INFO line containing {expected!r} in stderr:\n{cli_run['stderr']}"


_PROGRESS_LINE = re.compile(
    r"^INFO: (?:Running cargo (?P<start>package|publish --dry-run) for crate|"
    r"(?:(?P<packaged>Successfully packaged)|(?P<published>Dry-run publish "
    r"succeeded for)) crate) "
    r"(?P<crate>\S+) \((?P<index>\d+)/(?P<total>\d+)\)"
    r"(?P<elapsed> in \d+\.\ds)?$"
)


def _progress_phase(match: re.Match[str]) -> str:
    """Return ``package`` or ``publish`` for a matched progress line."""
    start = match["start"]
    if start is not None:
        return "package" if start == "package" else "publish"
    return "package" if match["packaged"] else "publish"


@then(
    parsers.parse(
        'the publish progress lines report crates "{crate_names}" with their '
        "positions and elapsed times"
    )
)
def then_publish_progress_lines(cli_run: CliRunResult, crate_names: str) -> None:
    """Assert start and success lines per crate and phase carry n/total and seconds.

    A dry run packages every crate and then publishes every crate, so each
    crate produces four lines: package start, package success, publish start,
    publish success. Start lines carry the position; success lines carry the
    position and the elapsed seconds.
    """
    expected_crates = _split_names(crate_names)
    total = len(expected_crates)
    observed = [
        (
            _progress_phase(match),
            match["crate"],
            int(match["index"]),
            int(match["total"]),
            bool(match["elapsed"]),
        )
        for line in cli_run["stderr"].splitlines()
        if (match := _PROGRESS_LINE.match(line))
    ]
    expected = [
        (phase, crate, index, total, has_elapsed)
        for phase in ("package", "publish")
        for index, crate in enumerate(expected_crates, start=1)
        for has_elapsed in (False, True)
    ]
    assert observed == expected, (
        f"expected {expected} progress records, observed {observed}\n"
        f"--- stderr ---\n{cli_run['stderr']}"
    )
