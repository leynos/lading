"""Then-step definitions for the printed publish plan.

``lading publish`` prints a plan before it does any work, and that plan is the
operator's only preview of what will happen. These steps assert the sections
it emits: the publishable crates and their order, the configuration-skipped
and manifest-skipped crates, the exclusions that matched nothing, and the
sections it correctly leaves out.
"""

from pytest_bdd import parsers, then

from .cli_run_types import CliRunResult
from .test_publish_helpers import (
    _assert_cli_run_succeeded,
    _publish_plan_lines,
    _split_names,
)

_CONFIGURATION_SKIP_HEADER = "Skipped via publish.exclude:"
_MANIFEST_SKIP_HEADER = "Skipped (publish = false):"


def _plan_section(lines: list[str], header: str) -> list[str]:
    """Return the plan lines following ``header`` up to the next blank gap."""
    assert header in lines, (
        f"the plan must include the {header!r} section for this scenario"
    )
    section_index = lines.index(header)
    return lines[section_index + 1 :]


@then(parsers.parse('the publish command prints the publish plan for "{crate_name}"'))
def then_publish_prints_plan(cli_run: CliRunResult, crate_name: str) -> None:
    """Assert that the publish command emits a publication plan summary."""
    _assert_cli_run_succeeded(cli_run)
    workspace = cli_run["workspace"]
    lines = _publish_plan_lines(cli_run)
    assert lines[0] == f"Publish plan for {workspace}", (
        "the plan must open by naming the workspace root"
    )
    assert lines[1].startswith("Strip patch strategy:"), (
        "the plan must state the strip patch strategy on its second line"
    )
    assert f"- {crate_name} @ 0.1.0" in lines, (
        "the plan must list the requested crate at its published version"
    )


@then("the publish command reports that no crates are publishable")
def then_publish_reports_none(cli_run: CliRunResult) -> None:
    """Assert that the publish command highlights the empty publish list."""
    _assert_cli_run_succeeded(cli_run)
    lines = _publish_plan_lines(cli_run)
    assert "Crates to publish: none" in lines, (
        "a workspace with nothing to publish must be reported as such"
    )


@then(parsers.parse('the publish command lists crates in order "{crate_names}"'))
def then_publish_lists_crates_in_order(cli_run: CliRunResult, crate_names: str) -> None:
    """Assert that publishable crates appear in the expected order."""
    expected = _split_names(crate_names)
    lines = _publish_plan_lines(cli_run)
    header = f"Crates to publish ({len(expected)}):"
    assert header in lines, (
        "the plan must head the publish list with the expected crate count"
    )
    section_index = lines.index(header)
    publish_lines: list[str] = []
    for line in lines[section_index + 1 :]:
        if not line.startswith("- "):
            break
        publish_lines.append(line[2:])
    actual = [entry.split(" @ ", 1)[0] for entry in publish_lines]
    assert actual == expected, (
        "the plan must list publishable crates in the expected order"
    )


@then(
    parsers.parse('the publish command reports manifest-skipped crate "{crate_name}"')
)
def then_publish_reports_manifest_skip(cli_run: CliRunResult, crate_name: str) -> None:
    """Assert the publish plan lists ``crate_name`` under manifest skips."""
    skipped = _plan_section(_publish_plan_lines(cli_run), _MANIFEST_SKIP_HEADER)
    assert f"- {crate_name}" in skipped, (
        f"crate {crate_name} must be listed under the manifest skips"
    )


@then(
    parsers.parse(
        'the publish command reports configuration-skipped crate "{crate_name}"'
    )
)
def then_publish_reports_configuration_skip(
    cli_run: CliRunResult, crate_name: str
) -> None:
    """Assert the publish plan lists ``crate_name`` under configuration skips."""
    skipped = _plan_section(_publish_plan_lines(cli_run), _CONFIGURATION_SKIP_HEADER)
    assert f"- {crate_name}" in skipped, (
        f"crate {crate_name} must be listed under the configuration skips"
    )


@then(
    parsers.parse(
        'the publish command reports configuration-skipped crates "{crate_names}"'
    )
)
def then_publish_reports_multiple_configuration_skips(
    cli_run: CliRunResult, crate_names: str
) -> None:
    """Assert the publish plan lists all configuration exclusions."""
    skipped = _plan_section(_publish_plan_lines(cli_run), _CONFIGURATION_SKIP_HEADER)
    for name in _split_names(crate_names):
        assert f"- {name}" in skipped, (
            f"crate {name} must be listed under the configuration skips"
        )


@then(parsers.parse('the publish command reports missing exclusion "{name}"'))
def then_publish_reports_missing_exclusion(cli_run: CliRunResult, name: str) -> None:
    """Assert the publish plan reports the missing exclusion ``name``."""
    missing = _plan_section(
        _publish_plan_lines(cli_run),
        "Configured exclusions not found in workspace:",
    )
    assert f"- {name}" in missing, (
        f"the unmatched exclusion {name} must be listed as missing"
    )


@then(parsers.parse('the publish command omits section "{header}"'))
def then_publish_omits_section(cli_run: CliRunResult, header: str) -> None:
    """Assert that the publish plan does not mention ``header``."""
    lines = _publish_plan_lines(cli_run)
    assert header not in lines, (
        "the plan must omit the section when nothing belongs in it"
    )
