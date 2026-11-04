"""Then-step definitions for the staged workspace's manifest and lifetime.

Staging rewrites the workspace copy before Cargo ever sees it: patch entries
are stripped according to configuration, and the copy is removed once the
publish completes. These steps assert on that rewritten manifest and on the
staging tree's survival.
"""

from pathlib import Path

from pytest_bdd import parsers, then

from .cli_run_types import CliRunResult
from .test_publish_helpers import (
    _assert_cli_run_succeeded,
    _extract_staging_root_from_plan,
    _get_patch_entries,
    _load_staged_manifest,
    _publish_plan_lines,
    _split_names,
)


@then("the publish staging manifest has no patch section")
def then_publish_manifest_has_no_patch_section(cli_run: CliRunResult) -> None:
    """Assert the staged manifest lacks ``[patch.crates-io]`` entirely."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    assert entries == {}, "the staged manifest must not carry any patch entries"


@then(
    parsers.re(
        r"the publish staging manifest (?P<verb>omits|retains) patch entries "
        r'"(?P<crate_names>[^"]*)"'
    )
)
def then_publish_manifest_patch_entries(
    cli_run: CliRunResult, verb: str, crate_names: str
) -> None:
    """Assert each crate's presence in the staged patch table matches the verb."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    retained = verb == "retains"
    for name in _split_names(crate_names):
        assert (name in entries) is retained, (
            f"crate {name} must be {'retained in' if retained else 'stripped from'} "
            "the staged patch table"
        )


@then("the staged workspace copy has been removed")
def then_staged_workspace_copy_removed(cli_run: CliRunResult) -> None:
    """Assert the publish removed the copy it announced in its plan.

    The copy is the whole workspace plus its verify build, and leaving it
    behind on a long-lived host is what issue #269 records. The path is read
    back out of the publish plan, so this measures the run rather than a
    convention about where staging happens.

    Parameters
    ----------
    cli_run : CliRunResult
        The captured publish run whose plan names the staging path.
    """
    staging_root = _staging_root(cli_run)
    assert not staging_root.exists(), f"{staging_root} survived the publish"


def _staging_root(cli_run: CliRunResult) -> Path:
    """Return the staging root a successful run's plan names."""
    _assert_cli_run_succeeded(cli_run)
    return _extract_staging_root_from_plan(_publish_plan_lines(cli_run))


def _assert_staging_paths(cli_run: CliRunResult, paths: str, *, retained: bool) -> None:
    """Assert each comma-separated path's presence matches ``retained``."""
    staging_root = _staging_root(cli_run)
    for relative in _split_names(paths):
        present = (staging_root / relative).exists()
        assert present is retained, (
            f"{relative} must be {'retained in' if retained else 'omitted from'} "
            "the staged tree"
        )


@then(
    parsers.re(
        r'the retained staging tree (?P<verb>omits|retains) paths "(?P<paths>[^"]*)"'
    )
)
def then_retained_staging_paths(cli_run: CliRunResult, verb: str, paths: str) -> None:
    """Assert that every comma-separated path's presence matches the verb."""
    _assert_staging_paths(cli_run, paths, retained=verb == "retains")
