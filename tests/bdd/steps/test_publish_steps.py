"""BDD steps focused on the publish subcommand."""

from __future__ import annotations

import dataclasses as dc
import os
import typing as typ
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when
from tomlkit import parse as parse_toml

from lading.commands import publish
from lading.workspace import metadata as metadata_module

from . import config_fixtures as _config_fixtures  # noqa: F401
from . import manifest_fixtures as _manifest_fixtures  # noqa: F401
from . import metadata_fixtures as _metadata_fixtures  # noqa: F401

try:
    from cmd_mox import CmdMox
except ModuleNotFoundError:
    CmdMox = typ.Any  # type: ignore[assignment]


if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from .test_common_steps import _run_cli  # noqa: F401
else:  # pragma: no cover - runtime fallback for typing helpers
    TOMLDocument = typ.Any  # type: ignore[assignment]


@dc.dataclass(frozen=True, slots=True)
class _CommandResponse:
    """Describe the outcome of a mocked command invocation."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dc.dataclass(slots=True)
class _PreflightInvocationRecorder:
    """Collect arguments recorded from cmd-mox double invocations."""

    records: list[tuple[str, tuple[str, ...], dict[str, str]]] = dc.field(
        default_factory=list
    )

    def record(self, label: str, args: tuple[str, ...], env: dict[str, str]) -> None:
        self.records.append((label, args, env))

    def by_label(self, label: str) -> list[tuple[tuple[str, ...], dict[str, str]]]:
        return [
            (args, env)
            for entry_label, args, env in self.records
            if entry_label == label
        ]


@dc.dataclass(frozen=True, slots=True)
class _PreflightStubConfig:
    """Configuration for cmd-mox preflight command stubs."""

    cmd_mox: CmdMox
    overrides: dict[tuple[str, ...], _CommandResponse] = dc.field(default_factory=dict)
    recorder: _PreflightInvocationRecorder | None = None


def _create_stub_config(
    cmd_mox: CmdMox,
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
    preflight_recorder: _PreflightInvocationRecorder,
) -> _PreflightStubConfig:
    """Build a stub configuration that records preflight invocations."""
    return _PreflightStubConfig(
        cmd_mox,
        preflight_overrides,
        recorder=preflight_recorder,
    )


class _CmdInvocation(typ.Protocol):
    """Protocol describing the cmd-mox invocation payload."""

    args: typ.Sequence[str]


@pytest.fixture
def preflight_overrides() -> dict[tuple[str, ...], _CommandResponse]:
    """Provide per-scenario overrides for publish pre-flight commands."""
    return {}


@pytest.fixture
def preflight_recorder() -> _PreflightInvocationRecorder:
    """Capture arguments passed to mocked pre-flight commands."""
    return _PreflightInvocationRecorder()


@given("cmd-mox IPC socket is unset")
def given_cmd_mox_socket_unset(
    monkeypatch: pytest.MonkeyPatch, cmd_mox: CmdMox
) -> None:
    """Ensure cmd-mox stub usage fails due to a missing socket variable."""
    from cmd_mox import environment as env_mod

    del cmd_mox
    monkeypatch.delenv(env_mod.CMOX_IPC_SOCKET_ENV, raising=False)
    monkeypatch.setenv(metadata_module.CMD_MOX_STUB_ENV_VAR, "1")


@when(
    "I run publish pre-flight checks for that workspace",
    target_fixture="preflight_result",
)
def when_run_publish_preflight_checks(workspace_directory: Path) -> dict[str, typ.Any]:
    """Execute publish pre-flight checks directly and capture failures."""
    error: publish.PublishPreflightError | None = None
    try:
        publish._run_preflight_checks(workspace_directory, allow_dirty=False)
    except publish.PublishPreflightError as exc:
        error = exc
    return {"error": error}


def _is_cargo_action_command(program: str, args: tuple[str, ...]) -> bool:
    """Check if command is a cargo check or test invocation."""
    return program == "cargo" and bool(args) and args[0] in {"check", "test"}


def _validate_stub_arguments(
    expected: tuple[str, ...],
    received: tuple[str, ...],
) -> None:
    """Validate that received arguments match the expected prefix."""
    if not expected:
        return

    if len(received) < len(expected):
        message = "Received fewer arguments than expected for preflight stub"
        raise AssertionError(message)

    for index, expected_arg in enumerate(expected):
        if expected_arg != received[index]:
            message = (
                "Preflight stub mismatch: expected argument prefix "
                f"{expected_arg!r} at position {index}, got "
                f"{received[index]!r}"
            )
            raise AssertionError(message)


def _resolve_preflight_expectation(
    command: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Return the cmd-mox program and argument prefix for ``command``."""
    program, *args = command
    argument_tuple = tuple(args)
    if program == "cargo" and argument_tuple and argument_tuple[0] in {"check", "test"}:
        return f"cargo::{argument_tuple[0]}", argument_tuple[1:]
    return program, argument_tuple


def _make_preflight_handler(
    response: _CommandResponse,
    expected_arguments: tuple[str, ...],
    recorder: _PreflightInvocationRecorder | None,
    label: str,
) -> typ.Callable[[_CmdInvocation], tuple[str, str, int]]:
    """Build a cmd-mox handler that validates argument prefixes."""

    def _handler(invocation: _CmdInvocation) -> tuple[str, str, int]:
        _validate_stub_arguments(expected_arguments, tuple(invocation.args))
        if recorder is not None:
            env_mapping = dict(getattr(invocation, "env", {}))
            recorder.record(label, tuple(invocation.args), env_mapping)
        return (response.stdout, response.stderr, response.exit_code)

    return _handler


def _register_preflight_commands(config: _PreflightStubConfig) -> None:
    """Install cmd-mox doubles for publish pre-flight commands."""
    defaults = {
        ("git", "status", "--porcelain"): _CommandResponse(exit_code=0),
        (
            "cargo",
            "check",
            "--workspace",
            "--all-targets",
        ): _CommandResponse(exit_code=0),
        (
            "cargo",
            "test",
            "--workspace",
        ): _CommandResponse(exit_code=0),
    }
    defaults.update(config.overrides)
    for command, response in defaults.items():
        expectation_program, expectation_args = _resolve_preflight_expectation(command)
        config.cmd_mox.stub(expectation_program).runs(
            _make_preflight_handler(
                response, expectation_args, config.recorder, expectation_program
            )
        )


def _invoke_publish_with_options(
    repo_root: Path,
    workspace_directory: Path,
    stub_config: _PreflightStubConfig,
    *extra_args: str,
) -> dict[str, typ.Any]:
    """Register preflight doubles, enable stubs, and run the CLI."""
    from .test_common_steps import _run_cli

    _register_preflight_commands(stub_config)
    previous = os.environ.get(metadata_module.CMD_MOX_STUB_ENV_VAR)
    os.environ[metadata_module.CMD_MOX_STUB_ENV_VAR] = "1"
    try:
        return _run_cli(repo_root, workspace_directory, "publish", *extra_args)
    finally:
        if previous is None:
            os.environ.pop(metadata_module.CMD_MOX_STUB_ENV_VAR, None)
        else:
            os.environ[metadata_module.CMD_MOX_STUB_ENV_VAR] = previous


@when("I invoke lading publish with that workspace", target_fixture="cli_run")
def when_invoke_lading_publish(
    workspace_directory: Path,
    repo_root: Path,
    cmd_mox: CmdMox,
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
    preflight_recorder: _PreflightInvocationRecorder,
) -> dict[str, typ.Any]:
    """Execute the publish CLI via ``python -m`` and capture the result."""
    stub_config = _create_stub_config(cmd_mox, preflight_overrides, preflight_recorder)
    return _invoke_publish_with_options(repo_root, workspace_directory, stub_config)


@when(
    "I invoke lading publish with that workspace using --forbid-dirty",
    target_fixture="cli_run",
)
def when_invoke_lading_publish_forbid_dirty(
    workspace_directory: Path,
    repo_root: Path,
    cmd_mox: CmdMox,
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
    preflight_recorder: _PreflightInvocationRecorder,
) -> dict[str, typ.Any]:
    """Execute the publish CLI with ``--forbid-dirty`` enabled."""
    stub_config = _create_stub_config(cmd_mox, preflight_overrides, preflight_recorder)
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--forbid-dirty",
    )


@given("cargo check fails during publish pre-flight")
def given_cargo_check_fails(
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
) -> None:
    """Simulate a failing cargo check command."""
    preflight_overrides[("cargo", "check", "--workspace", "--all-targets")] = (
        _CommandResponse(exit_code=1, stderr="cargo check failed")
    )


@given("cargo test fails during publish pre-flight")
def given_cargo_test_fails(
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
) -> None:
    """Simulate a failing cargo test command."""
    preflight_overrides[("cargo", "test", "--workspace")] = _CommandResponse(
        exit_code=1, stderr="cargo test failed"
    )


@given(parsers.parse('cargo test fails with compiletest artifact "{relative_path}"'))
def given_cargo_test_fails_with_artifact(
    workspace_directory: Path,
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
    relative_path: str,
) -> None:
    """Create ``relative_path`` and configure cargo test to reference it."""
    artifact = workspace_directory / relative_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("line1\nline2\n", encoding="utf-8")
    preflight_overrides[("cargo", "test", "--workspace")] = _CommandResponse(
        exit_code=1,
        stderr=f"diff at {artifact}",
    )


@given("the workspace has uncommitted changes")
def given_workspace_dirty(
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
) -> None:
    """Simulate a dirty working tree for git status."""
    preflight_overrides[("git", "status", "--porcelain")] = _CommandResponse(
        exit_code=0,
        stdout=" M Cargo.toml\n",
    )


@given(
    parsers.re(
        r'the preflight command "(?P<command>.+)" exits with '
        r'code (?P<exit_code>\d+) and stderr "(?P<stderr>.*)"'
    )
)
def given_preflight_command_override(
    preflight_overrides: dict[tuple[str, ...], _CommandResponse],
    command: str,
    exit_code: str,
    stderr: str,
) -> None:
    """Override an arbitrary pre-flight command with a custom result."""
    if tokens := tuple(segment for segment in command.split() if segment):
        preflight_overrides[tokens] = _CommandResponse(
            exit_code=int(exit_code),
            stderr=stderr,
        )
    else:
        message = "preflight command override requires tokens"
        raise AssertionError(message)


@then(parsers.parse('the publish command prints the publish plan for "{crate_name}"'))
def then_publish_prints_plan(cli_run: dict[str, typ.Any], crate_name: str) -> None:
    """Assert that the publish command emits a publication plan summary."""
    assert cli_run["returncode"] == 0
    workspace = cli_run["workspace"]
    lines = [line.strip() for line in cli_run["stdout"].splitlines() if line.strip()]
    assert lines[0] == f"Publish plan for {workspace}"
    assert lines[1].startswith("Strip patch strategy:")
    assert f"- {crate_name} @ 0.1.0" in lines


def _load_staged_manifest(cli_run: dict[str, typ.Any]) -> TOMLDocument:
    """Return the staged workspace manifest for ``cli_run``."""
    lines = _publish_plan_lines(cli_run)
    staging_root = _extract_staging_root_from_plan(lines)
    manifest_path = staging_root / "Cargo.toml"
    if not manifest_path.exists():
        message = f"Staged manifest not found: {manifest_path}"
        raise AssertionError(message)
    return parse_toml(manifest_path.read_text(encoding="utf-8"))


def _get_patch_entries(document: typ.Mapping[str, typ.Any]) -> dict[str, typ.Any]:
    """Return the ``[patch.crates-io]`` mapping if it exists."""
    patch_table = document.get("patch")
    if not isinstance(patch_table, typ.Mapping):
        return {}
    crates_io = patch_table.get("crates-io")
    return {} if not isinstance(crates_io, typ.Mapping) else dict(crates_io)


@then("the publish staging manifest has no patch section")
def then_publish_manifest_has_no_patch_section(cli_run: dict[str, typ.Any]) -> None:
    """Assert the staged manifest lacks ``[patch.crates-io]`` entirely."""
    document = _load_staged_manifest(cli_run)
    entries = _get_patch_entries(document)
    assert entries == {}


def _split_names(crate_names: str) -> list[str]:
    return [name.strip() for name in crate_names.split(",") if name.strip()]


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


def _get_test_invocations(
    recorder: _PreflightInvocationRecorder,
) -> list[tuple[str, ...]]:
    """Return recorded cargo test invocations or raise if missing."""
    if invocations := recorder.by_label("cargo::test"):
        return [args for args, _ in invocations]
    message = "cargo test pre-flight command was not invoked"
    raise AssertionError(message)


def _get_test_invocation_envs(
    recorder: _PreflightInvocationRecorder,
) -> list[dict[str, str]]:
    """Return recorded cargo test environments or raise if missing."""
    if invocations := recorder.by_label("cargo::test"):
        return [env for _, env in invocations]
    message = "cargo test pre-flight command was not invoked"
    raise AssertionError(message)


def _has_contiguous_args(args: tuple[str, ...], first: str, second: str) -> bool:
    """Return True when ``first`` is immediately followed by ``second`` in ``args``."""
    for index in range(len(args) - 1):
        if args[index] == first and args[index + 1] == second:
            return True
    return False


def _has_ordered_args_non_contiguous(
    args: tuple[str, ...], first: str, second: str
) -> bool:
    """Return True when ``first`` appears before ``second`` in ``args``."""
    try:
        start_index = args.index(first)
    except ValueError:
        return False
    return second in args[start_index + 1 :]


def _has_ordered_args(
    invocations: list[tuple[str, ...]],
    first: str,
    second: str,
    *,
    contiguous: bool = True,
) -> bool:
    """Detect ``first`` followed by ``second`` in ``invocations``."""
    checker = _has_contiguous_args if contiguous else _has_ordered_args_non_contiguous
    return any(checker(args, first, second) for args in invocations)


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
    if not _has_ordered_args(test_invocations, "--exclude", crate_name):
        message = (
            f"Expected --exclude {crate_name!r} in cargo test pre-flight invocations"
        )
        raise AssertionError(message)


@then("the publish command limits pre-flight tests to libraries and binaries")
def then_publish_limits_preflight_targets(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that cargo test pre-flight invocations pass --lib and --bins."""
    test_invocations = _get_test_invocations(preflight_recorder)
    if not _has_ordered_args(test_invocations, "--lib", "--bins"):
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
    if not any(environment.get(name) == value for environment in envs):
        message = f"Expected cargo test env {name}={value!r}"
        raise AssertionError(message)


@then(parsers.parse('the cargo test pre-flight env includes "{snippet}" in RUSTFLAGS'))
def then_cargo_test_env_rustflags_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    snippet: str,
) -> None:
    """Assert that cargo test RUSTFLAGS contains ``snippet``."""
    envs = _get_test_invocation_envs(preflight_recorder)
    if not any(snippet in environment.get("RUSTFLAGS", "") for environment in envs):
        message = f"Expected {snippet!r} in cargo test RUSTFLAGS"
        raise AssertionError(message)


@then(parsers.parse('the publish command lists crates in order "{crate_names}"'))
def then_publish_lists_crates_in_order(
    cli_run: dict[str, typ.Any], crate_names: str
) -> None:
    """Assert that publishable crates appear in the expected order."""
    expected = [name.strip() for name in crate_names.split(",") if name.strip()]
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


@then("the publish command reports that no crates are publishable")
def then_publish_reports_none(cli_run: dict[str, typ.Any]) -> None:
    """Assert that the publish command highlights the empty publish list."""
    assert cli_run["returncode"] == 0
    lines = _publish_plan_lines(cli_run)
    assert "Crates to publish: none" in lines


def _publish_plan_lines(cli_run: dict[str, typ.Any]) -> list[str]:
    """Return trimmed publish plan output lines for ``cli_run``."""
    return [line.strip() for line in cli_run["stdout"].splitlines() if line.strip()]


def _extract_staging_root_from_plan(lines: list[str]) -> Path:
    """Return the staging root path parsed from publish plan ``lines``."""
    staging_line = next(
        (line for line in lines if line.startswith("Staged workspace at:")), None
    )
    assert staging_line is not None, "Staging location not found in publish plan output"
    return Path(staging_line.split(": ", 1)[1])


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

    # The formatting helper reports relative paths when possible, so verify
    # that the corresponding staged README exists where the CLI claims.
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
