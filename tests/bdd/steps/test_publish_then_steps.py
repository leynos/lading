"""Then-step definitions for publish BDD scenarios.

Implements :mod:`pytest_bdd` *then* steps that assert post-command
outcomes for ``lading publish`` scenarios defined in
``tests/bdd/features/cli.feature``.

Step inventory
--------------
``then_publish_interleaves_live_package_and_publish(preflight_recorder, crate_names)``
    Filters recorded invocations to ``cargo::package`` and
    ``cargo::publish`` operations, derives the observed crate name from
    each invocation's ``PWD`` directory basename, and asserts that the
    sequence matches the expected interleaved order for the
    comma-separated ``crate_names``.

Related step modules
--------------------
``test_publish_given_steps``
    *Given* steps that configure the workspace and publish plan.
``test_publish_when_steps``
    *When* steps that invoke the CLI command under test.
``test_publish_helpers``
    Shared assertion helpers used across given/when/then step modules.
"""

from __future__ import annotations

import json
import re
import typing as typ

from pytest_bdd import parsers, then

from .test_publish_helpers import (
    _assert_cli_run_succeeded,
    _assert_crate_order_matches,
    _assert_invocations_have_flag,
    _assert_invocations_lack_flag,
    _extract_crate_names_from_invocations,
    _get_patch_entries,
    _get_required_invocations,
    _has_contiguous_args,
    _load_staged_manifest,
    _publish_plan_lines,
    _split_names,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from .cli_run_types import CliRunResult
    from .test_publish_infrastructure import _PreflightInvocationRecorder


@then(parsers.parse('the publish command prints the publish plan for "{crate_name}"'))
def then_publish_prints_plan(cli_run: CliRunResult, crate_name: str) -> None:
    """Assert that the publish command emits a publication plan summary."""
    _assert_cli_run_succeeded(cli_run)
    workspace = cli_run["workspace"]
    lines = _publish_plan_lines(cli_run)
    assert lines[0] == f"Publish plan for {workspace}"
    assert lines[1].startswith("Strip patch strategy:")
    assert f"- {crate_name} @ 0.1.0" in lines


@then("the publish staging manifest has no patch section")
def then_publish_manifest_has_no_patch_section(cli_run: CliRunResult) -> None:
    """Assert the staged manifest lacks ``[patch.crates-io]`` entirely."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    assert entries == {}


@then(parsers.parse('the publish staging manifest omits patch entries "{crate_names}"'))
def then_publish_manifest_omits_entries(
    cli_run: CliRunResult, crate_names: str
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
    cli_run: CliRunResult, crate_names: str
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
    """Assert that cargo test pre-flight invocations skip ``crate_name``.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    crate_name : str
        Crate expected to be excluded from the cargo test pre-flight.

    Raises
    ------
    AssertionError
        If no cargo test pre-flight invocation was recorded at all, or
        if no pre-flight invocation excludes ``crate_name``.
    """
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::test",
        "cargo test pre-flight command was not invoked",
    )
    test_invocations = [args for args, _ in invocations]
    if not any(_has_ordered_args_single(args, crate_name) for args in test_invocations):
        message = (
            f"Expected --exclude {crate_name!r} in cargo test pre-flight invocations"
        )
        raise AssertionError(message)


def _has_ordered_args_single(args: tuple[str, ...], crate_name: str) -> bool:
    """Check for a contiguous ``--exclude`` immediately followed by ``crate_name``."""
    return _has_contiguous_args(args, "--exclude", crate_name)


@then("the publish command limits pre-flight tests to libraries and binaries")
def then_publish_limits_preflight_targets(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that cargo test pre-flight invocations pass --lib and --bins.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.

    Raises
    ------
    AssertionError
        If no cargo test pre-flight invocation was recorded at all, or
        if no invocation passes ``--lib`` followed by ``--bins``.
    """
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::test",
        "cargo test pre-flight command was not invoked",
    )
    test_invocations = [args for args, _ in invocations]
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
    """Assert that cargo test pre-flight invocations omit --exclude.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.

    Raises
    ------
    AssertionError
        If no cargo test pre-flight invocation was recorded at all, or
        if any invocation passes ``--exclude``.
    """
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::test",
        "cargo test pre-flight command was not invoked",
    )
    test_invocations = [args for args, _ in invocations]
    for args in test_invocations:
        if "--exclude" in args:
            message = "Did not expect --exclude arguments in cargo test pre-flight"
            raise AssertionError(message)


@then(parsers.parse('the publish command runs auxiliary build "{label}"'))
def then_publish_runs_aux_build(
    preflight_recorder: _PreflightInvocationRecorder,
    label: str,
) -> None:
    """Assert that an auxiliary build command was executed.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    label : str
        Recorder label identifying the expected auxiliary build.

    Raises
    ------
    AssertionError
        If no auxiliary build invocation matches ``label``.
    """
    if not preflight_recorder.by_label(label):
        message = f"Expected auxiliary build invocation for {label}"
        raise AssertionError(message)


@then(parsers.parse('the cargo test pre-flight env contains "{name}"="{value}"'))
def then_cargo_test_env_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    name: str,
    value: str,
) -> None:
    """Assert that cargo test env propagates ``name`` with ``value``.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    name : str
        Environment variable expected on a cargo test invocation.
    value : str
        Value expected for ``name``.

    Raises
    ------
    AssertionError
        If no cargo test pre-flight invocation was recorded at all, or
        if no invocation environment maps ``name`` to ``value``.
    """
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::test",
        "cargo test pre-flight command was not invoked",
    )
    envs = [env for _, env in invocations]
    if all(environment.get(name) != value for environment in envs):
        message = f"Expected cargo test env {name}={value!r}"
        raise AssertionError(message)


@then(parsers.parse('the cargo test pre-flight env includes "{snippet}" in RUSTFLAGS'))
def then_cargo_test_env_rustflags_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    snippet: str,
) -> None:
    """Assert that cargo test RUSTFLAGS contains ``snippet``.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    snippet : str
        Fragment expected within a cargo test ``RUSTFLAGS`` value.

    Raises
    ------
    AssertionError
        If no cargo test pre-flight invocation was recorded at all, or
        if no invocation's ``RUSTFLAGS`` contains ``snippet``.
    """
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::test",
        "cargo test pre-flight command was not invoked",
    )
    envs = [env for _, env in invocations]
    if all(snippet not in environment.get("RUSTFLAGS", "") for environment in envs):
        message = f"Expected {snippet!r} in cargo test RUSTFLAGS"
        raise AssertionError(message)


@then(parsers.parse('the publish command lists crates in order "{crate_names}"'))
def then_publish_lists_crates_in_order(cli_run: CliRunResult, crate_names: str) -> None:
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
    """Assert that cargo package ran for each crate in publish order.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    crate_names : str
        Comma-separated crate names in their expected package order.

    Raises
    ------
    AssertionError
        If no ``cargo::package`` invocations were recorded, or the
        packaged crate order does not match the expected publish order.
    """  # ruff: ignore[docstring-extraneous-exception]
    expected = [name.strip() for name in crate_names.split(",") if name.strip()]
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::package",
        "cargo package was not invoked for publishable crates",
    )
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
    """Assert that cargo publish --dry-run runs for each crate in order.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    crate_names : str
        Comma-separated crate names in their expected publish order.

    Raises
    ------
    AssertionError
        If no ``cargo::publish`` invocations were recorded, an invocation
        is missing the ``--dry-run`` flag, or the crate order does not
        match.
    """  # ruff: ignore[docstring-extraneous-exception]
    expected = _split_names(crate_names)
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::publish",
        "cargo publish was not invoked for publishable crates",
    )
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
    """Assert that live cargo publish runs without the dry-run flag.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    crate_names : str
        Comma-separated crate names in their expected publish order.

    Raises
    ------
    AssertionError
        If no ``cargo::publish`` invocations were recorded, an invocation
        unexpectedly carries the ``--dry-run`` flag, or the crate order
        does not match.
    """  # ruff: ignore[docstring-extraneous-exception]
    expected = _split_names(crate_names)
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::publish",
        "cargo publish was not invoked for publishable crates",
    )
    _assert_invocations_lack_flag(invocations, "--dry-run", "cargo publish")
    observed = _extract_crate_names_from_invocations(invocations)
    _assert_crate_order_matches(observed, expected, "cargo publish live order")


@then(
    parsers.parse(
        "the publish command interleaves live package and publish for crates "
        '"{crate_names}"'
    )
)
def then_publish_interleaves_live_package_and_publish(
    preflight_recorder: _PreflightInvocationRecorder, crate_names: str
) -> None:
    """Assert live publish packages and publishes each crate before the next.

    Parameters
    ----------
    preflight_recorder : _PreflightInvocationRecorder
        Recorder holding the captured pre-flight command invocations.
    crate_names : str
        Comma-separated crate names in their expected publish order.

    Raises
    ------
    AssertionError
        If the observed package/publish order differs from the expected one.
    """
    expected_names = _split_names(crate_names)
    filtered = [
        (label, (args, env))
        for label, args, env in preflight_recorder.records
        if label in {"cargo::package", "cargo::publish"}
    ]
    labels = [label for label, _ in filtered]
    invocations = [invocation for _, invocation in filtered]
    crate_names_observed = _extract_crate_names_from_invocations(invocations)
    observed_sequence = list(zip(labels, crate_names_observed, strict=True))

    expected_sequence: list[tuple[str, str]] = []
    for crate_name in expected_names:
        expected_sequence.extend([
            ("cargo::package", crate_name),
            ("cargo::publish", crate_name),
        ])

    if observed_sequence != expected_sequence:
        message = (
            "Unexpected live package/publish order: "
            f"observed={observed_sequence!r}, expected={expected_sequence!r}"
        )
        raise AssertionError(message)


@then("the publish command reports that no crates are publishable")
def then_publish_reports_none(cli_run: CliRunResult) -> None:
    """Assert that the publish command highlights the empty publish list."""
    _assert_cli_run_succeeded(cli_run)
    lines = _publish_plan_lines(cli_run)
    assert "Crates to publish: none" in lines


@then(
    parsers.parse('the publish command reports manifest-skipped crate "{crate_name}"')
)
def then_publish_reports_manifest_skip(cli_run: CliRunResult, crate_name: str) -> None:
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
    cli_run: CliRunResult, crate_name: str
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
    cli_run: CliRunResult, crate_names: str
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
def then_publish_reports_missing_exclusion(cli_run: CliRunResult, name: str) -> None:
    """Assert the publish plan reports the missing exclusion ``name``."""
    lines = _publish_plan_lines(cli_run)
    assert "Configured exclusions not found in workspace:" in lines
    section_index = lines.index("Configured exclusions not found in workspace:")
    missing = lines[section_index + 1 :]
    assert f"- {name}" in missing


@then(parsers.parse('the publish command omits section "{header}"'))
def then_publish_omits_section(cli_run: CliRunResult, header: str) -> None:
    """Assert that the publish plan does not mention ``header``."""
    lines = _publish_plan_lines(cli_run)
    assert header not in lines


@then("the command should not raise a preflight error about the flag")
def then_publish_flag_is_accepted(cli_run: CliRunResult) -> None:
    """Assert that the dry-run override flag does not fail pre-flight."""
    _assert_cli_run_succeeded(cli_run)
    assert "--allow-unpublished-workspace-deps is only valid" not in cli_run["stderr"]


@then("a PublishPreflightError should be raised")
def then_publish_preflight_error_is_reported(cli_run: CliRunResult) -> None:
    """Assert that the CLI surfaced a publish pre-flight failure."""
    assert cli_run["returncode"] == 1


@then(parsers.parse('the error message should contain "{expected}"'))
def then_publish_error_message_contains(cli_run: CliRunResult, expected: str) -> None:
    """Assert that the CLI error output contains ``expected``."""
    assert expected in cli_run["stderr"]


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


@then("no PublishPreflightError should be raised")
def then_publish_preflight_error_is_not_reported(cli_run: CliRunResult) -> None:
    """Assert that publish completed without a pre-flight failure."""
    _assert_cli_run_succeeded(cli_run)
    assert "PublishPreflightError" not in cli_run["stderr"], (
        f"publish should complete without a pre-flight failure:\n{cli_run['stderr']}"
    )


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
    matches = [
        match
        for line in cli_run["stderr"].splitlines()
        if (match := _PROGRESS_LINE.match(line))
    ]
    observed = [
        (
            _progress_phase(match),
            match["crate"],
            int(match["index"]),
            int(match["total"]),
            bool(match["elapsed"]),
        )
        for match in matches
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


_SCCACHE_JSON_QUERY = ("--show-stats", "--stats-format=json")
_SCCACHE_TEXT_QUERY = ("--show-stats",)
_CARGO_BUILD_LABELS = frozenset({"cargo::package", "cargo::publish"})


@then(
    "sccache statistics were queried before the first cargo package and after "
    "every cargo invocation"
)
def then_sccache_queries_bracket_cargo_invocations(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert the baseline, per-invocation, and final sccache queries.

    The recorder lists every stubbed invocation in call order, so the
    expected shape is: one JSON query, then for each cargo package/publish a
    cargo call followed by a JSON query, then one plain ``--show-stats``.
    """
    sequence = [
        (label, tuple(args))
        for label, args, _env in preflight_recorder.records
        if label == "sccache" or label in _CARGO_BUILD_LABELS
    ]
    cargo_calls = [entry for entry in sequence if entry[0] in _CARGO_BUILD_LABELS]
    assert cargo_calls, "expected cargo package/publish invocations"
    expected: list[tuple[str, tuple[str, ...]]] = [("sccache", _SCCACHE_JSON_QUERY)]
    for entry in cargo_calls:
        expected.extend((entry, ("sccache", _SCCACHE_JSON_QUERY)))
    expected.append(("sccache", _SCCACHE_TEXT_QUERY))
    assert sequence == expected, f"unexpected query order:\n{sequence}"


@then(parsers.parse('the compiler-cache report "{name}" lists every cargo invocation'))
def then_sccache_report_lists_every_invocation(
    cli_run: CliRunResult,
    preflight_recorder: _PreflightInvocationRecorder,
    name: str,
) -> None:
    """Assert the JSON report carries one record per cargo package/publish."""
    report = json.loads((cli_run["workspace"] / name).read_text(encoding="utf-8"))
    cargo_count = sum(
        1
        for label, _args, _env in preflight_recorder.records
        if label in _CARGO_BUILD_LABELS
    )
    assert set(report) == {"wrapper", "baseline", "final", "crates", "delta"}, (
        f"report schema drifted: {sorted(report)}"
    )
    assert report["wrapper"] == "sccache", f"wrapper recorded as {report['wrapper']!r}"
    assert len(report["crates"]) == cargo_count, (
        f"expected one record per cargo invocation ({cargo_count}), "
        f"got {len(report['crates'])}"
    )
    assert {record["subcommand"] for record in report["crates"]} == {
        "package",
        "publish",
    }, "records should cover both the package and the publish phase"
    assert all(
        record["requests"] == 10 and record["hits"] == 8 and record["misses"] == 2
        for record in report["crates"]
    ), f"each record should carry the stub's per-query delta: {report['crates']}"
    assert report["delta"]["requests"] == 10 * cargo_count, (
        f"pipeline delta should sum the per-invocation deltas: {report['delta']}"
    )
