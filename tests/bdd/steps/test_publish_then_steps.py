"""Then steps for publish BDD scenarios."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from pytest_bdd import parsers, then

from lading.commands import publish

from .test_publish_helpers import (
    _assert_crate_order_matches,
    _assert_invocations_have_flag,
    _assert_invocations_lack_flag,
    _extract_crate_names_from_invocations,
    _extract_staging_root_from_plan,
    _get_package_invocations,
    _get_patch_entries,
    _get_publish_invocations,
    _get_test_invocation_envs,
    _get_test_invocations,
    _has_contiguous_args,
    _load_staged_manifest,
    _publish_plan_lines,
    _split_names,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from .test_publish_infrastructure import _PreflightInvocationRecorder


@then(parsers.parse('the publish command prints the publish plan for "{crate_name}"'))
def then_publish_prints_plan(cli_run: dict[str, typ.Any], crate_name: str) -> None:
    """Assert that the publish command emits a publication plan summary."""
    assert cli_run["returncode"] == 0
    workspace = cli_run["workspace"]
    lines = _publish_plan_lines(cli_run)
    assert lines[0] == f"Publish plan for {workspace}"
    assert lines[1].startswith("Strip patch strategy:")
    assert f"- {crate_name} @ 0.1.0" in lines


@then("the publish staging manifest has no patch section")
def then_publish_manifest_has_no_patch_section(cli_run: dict[str, typ.Any]) -> None:
    """Assert the staged manifest lacks ``[patch.crates-io]`` entirely."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    assert entries == {}


@then(parsers.parse('the publish staging manifest omits patch entries "{crate_names}"'))
def then_publish_manifest_omits_entries(
    cli_run: dict[str, typ.Any], crate_names: str
) -> None:
    """Assert that ``crate_names`` are absent from the staged patch table."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    for name in _split_names(crate_names):
        assert name not in entries


@then(
    parsers.parse('the publish staging manifest retains patch entries "{crate_names}"')
)
def then_publish_manifest_retains_entries(
    cli_run: dict[str, typ.Any], crate_names: str
) -> None:
    """Assert that ``crate_names`` remain in the staged patch table."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    for name in _split_names(crate_names):
        assert name in entries


@then(
    parsers.parse(
        'the publish command excludes crate "{crate_name}" from pre-flight tests'
    )
)
def then_publish_excludes_preflight_crate(
    preflight_recorder: _PreflightInvocationRecorder,
    crate_name: str,
) -> None:
    """Assert that cargo test pre-flight invocations skip ``crate_name``."""
    test_invocations = _get_test_invocations(preflight_recorder)
    if not any(_has_ordered_args_single(args, crate_name) for args in test_invocations):
        message = (
            f"Expected --exclude {crate_name!r} in cargo test pre-flight invocations"
        )
        raise AssertionError(message)


def _has_ordered_args_single(args: tuple[str, ...], crate_name: str) -> bool:
    """Check for contiguous --exclude <crate_name> pair."""
    return _has_contiguous_args(args, "--exclude", crate_name)


@then("the publish command limits pre-flight tests to libraries and binaries")
def then_publish_limits_preflight_targets(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that cargo test pre-flight invocations pass --lib and --bins."""
    test_invocations = _get_test_invocations(preflight_recorder)
    if not any(
        _has_contiguous_args(args, "--lib", "--bins") for args in test_invocations
    ):
        message = (
            "Expected --lib followed by --bins in cargo test pre-flight invocations"
        )
        raise AssertionError(message)


@then("the publish command does not add pre-flight excludes")
def then_publish_has_no_preflight_excludes(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that cargo test pre-flight invocations omit --exclude."""
    test_invocations = _get_test_invocations(preflight_recorder)
    for args in test_invocations:
        if "--exclude" in args:
            message = "Did not expect --exclude arguments in cargo test pre-flight"
            raise AssertionError(message)


@then(parsers.parse('the publish command runs auxiliary build "{label}"'))
def then_publish_runs_aux_build(
    preflight_recorder: _PreflightInvocationRecorder,
    label: str,
) -> None:
    """Assert that an auxiliary build command was executed."""
    if not preflight_recorder.by_label(label):
        message = f"Expected auxiliary build invocation for {label}"
        raise AssertionError(message)


@then(parsers.parse('the cargo test pre-flight env contains "{name}"="{value}"'))
def then_cargo_test_env_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    name: str,
    value: str,
) -> None:
    """Assert that cargo test env propagates ``name`` with ``value``."""
    envs = _get_test_invocation_envs(preflight_recorder)
    if all(environment.get(name) != value for environment in envs):
        message = f"Expected cargo test env {name}={value!r}"
        raise AssertionError(message)


@then(parsers.parse('the cargo test pre-flight env includes "{snippet}" in RUSTFLAGS'))
def then_cargo_test_env_rustflags_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    snippet: str,
) -> None:
    """Assert that cargo test RUSTFLAGS contains ``snippet``."""
    envs = _get_test_invocation_envs(preflight_recorder)
    if all(snippet not in environment.get("RUSTFLAGS", "") for environment in envs):
        message = f"Expected {snippet!r} in cargo test RUSTFLAGS"
        raise AssertionError(message)


@then(parsers.parse('the publish command lists crates in order "{crate_names}"'))
def then_publish_lists_crates_in_order(
    cli_run: dict[str, typ.Any], crate_names: str
) -> None:
    """Assert that publishable crates appear in the expected order."""
    expected = _split_names(crate_names)
    lines = _publish_plan_lines(cli_run)
    header = f"Crates to publish ({len(expected)}):"
    assert header in lines
    section_index = lines.index(header)
    publish_lines: list[str] = []
    for line in lines[section_index + 1 :]:
        if not line.startswith("- "):
            break
        publish_lines.append(line[2:])
    actual = [entry.split(" @ ", 1)[0] for entry in publish_lines]
    assert actual == expected


@then(parsers.parse('the publish command packages crates in order "{crate_names}"'))
def then_publish_packages_crates_in_order(
    preflight_recorder: _PreflightInvocationRecorder,
    crate_names: str,
) -> None:
    """Assert that cargo package ran for each crate in publish order."""
    expected = [name.strip() for name in crate_names.split(",") if name.strip()]
    invocations = _get_package_invocations(preflight_recorder)
    observed = _extract_crate_names_from_invocations(invocations)
    _assert_crate_order_matches(observed, expected, "cargo package")


@then(
    parsers.parse(
        'the publish command performs cargo publish dry-run for crates "{crate_names}"'
    )
)
def then_publish_runs_dry_run(
    preflight_recorder: _PreflightInvocationRecorder, crate_names: str
) -> None:
    """Assert that cargo publish --dry-run runs for each crate in order."""
    expected = _split_names(crate_names)
    invocations = _get_publish_invocations(preflight_recorder)
    _assert_invocations_have_flag(invocations, "--dry-run", "cargo publish")
    observed = _extract_crate_names_from_invocations(invocations)
    _assert_crate_order_matches(observed, expected, "cargo publish --dry-run order")


@then(
    parsers.parse(
        'the publish command performs live cargo publish for crates "{crate_names}"'
    )
)
def then_publish_runs_live(
    preflight_recorder: _PreflightInvocationRecorder, crate_names: str
) -> None:
    """Assert that live cargo publish runs without the dry-run flag."""
    expected = _split_names(crate_names)
    invocations = _get_publish_invocations(preflight_recorder)
    _assert_invocations_lack_flag(invocations, "--dry-run", "cargo publish")
    observed = _extract_crate_names_from_invocations(invocations)
    _assert_crate_order_matches(observed, expected, "cargo publish live order")


@then("the publish command reports that no crates are publishable")
def then_publish_reports_none(cli_run: dict[str, typ.Any]) -> None:
    """Assert that the publish command highlights the empty publish list."""
    assert cli_run["returncode"] == 0
    lines = _publish_plan_lines(cli_run)
    assert "Crates to publish: none" in lines


@then(
    parsers.parse('the publish command reports manifest-skipped crate "{crate_name}"')
)
def then_publish_reports_manifest_skip(
    cli_run: dict[str, typ.Any], crate_name: str
) -> None:
    """Assert the publish plan lists ``crate_name`` under manifest skips."""
    lines = _publish_plan_lines(cli_run)
    assert "Skipped (publish = false):" in lines
    section_index = lines.index("Skipped (publish = false):")
    skipped = lines[section_index + 1 :]
    assert f"- {crate_name}" in skipped


@then(
    parsers.parse(
        'the publish command reports configuration-skipped crate "{crate_name}"'
    )
)
def then_publish_reports_configuration_skip(
    cli_run: dict[str, typ.Any], crate_name: str
) -> None:
    """Assert the publish plan lists ``crate_name`` under configuration skips."""
    lines = _publish_plan_lines(cli_run)
    assert "Skipped via publish.exclude:" in lines
    section_index = lines.index("Skipped via publish.exclude:")
    skipped = lines[section_index + 1 :]
    assert f"- {crate_name}" in skipped


@then(
    parsers.parse(
        'the publish command reports configuration-skipped crates "{crate_names}"'
    )
)
def then_publish_reports_multiple_configuration_skips(
    cli_run: dict[str, typ.Any], crate_names: str
) -> None:
    """Assert the publish plan lists all configuration exclusions."""
    expected_names = [name.strip() for name in crate_names.split(",") if name.strip()]
    lines = _publish_plan_lines(cli_run)
    assert "Skipped via publish.exclude:" in lines
    section_index = lines.index("Skipped via publish.exclude:")
    skipped = lines[section_index + 1 :]
    for name in expected_names:
        assert f"- {name}" in skipped


@then(parsers.parse('the publish command reports missing exclusion "{name}"'))
def then_publish_reports_missing_exclusion(
    cli_run: dict[str, typ.Any], name: str
) -> None:
    """Assert the publish plan reports the missing exclusion ``name``."""
    lines = _publish_plan_lines(cli_run)
    assert "Configured exclusions not found in workspace:" in lines
    section_index = lines.index("Configured exclusions not found in workspace:")
    missing = lines[section_index + 1 :]
    assert f"- {name}" in missing


@then(parsers.parse('the publish command omits section "{header}"'))
def then_publish_omits_section(cli_run: dict[str, typ.Any], header: str) -> None:
    """Assert that the publish plan does not mention ``header``."""
    lines = _publish_plan_lines(cli_run)
    assert header not in lines


@then(
    parsers.parse(
        'the publish staging directory for crate "{crate_name}" '
        "contains the workspace README"
    )
)
def then_publish_staging_contains_readme(
    cli_run: dict[str, typ.Any], crate_name: str
) -> None:
    """Assert that staging propagated the workspace README into ``crate_name``."""
    lines = _publish_plan_lines(cli_run)
    staging_root = _extract_staging_root_from_plan(lines)
    staged_readme = staging_root / "crates" / crate_name / "README.md"
    assert staged_readme.exists()

    workspace_root = Path(cli_run["workspace"])
    source_readme = workspace_root / "README.md"
    assert source_readme.exists()
    assert staged_readme.read_text(encoding="utf-8") == source_readme.read_text(
        encoding="utf-8"
    )


@then(
    parsers.parse(
        'the publish plan lists copied workspace README for crate "{crate_name}"'
    )
)
def then_publish_lists_copied_readme(
    cli_run: dict[str, typ.Any], crate_name: str
) -> None:
    """Assert that the publish plan lists the staged README for ``crate_name``."""
    lines = _publish_plan_lines(cli_run)
    staging_root = _extract_staging_root_from_plan(lines)
    expected_relative = Path("crates") / crate_name / "README.md"
    expected_entry = f"- {expected_relative.as_posix()}"
    assert expected_entry in lines

    staged_readme = staging_root / expected_relative
    assert staged_readme.exists()


@then(
    "the publish pre-flight error contains "
    '"cmd-mox stub requested for publish pre-flight but CMOX_IPC_SOCKET is unset"'
)
def then_publish_preflight_reports_missing_socket(
    preflight_result: dict[str, typ.Any],
) -> None:
    """Assert that publish pre-flight checks report the missing socket."""
    error = preflight_result.get("error")
    assert isinstance(error, publish.PublishPreflightError)
    assert (
        "cmd-mox stub requested for publish pre-flight but CMOX_IPC_SOCKET is unset"
        in str(error)
    )
