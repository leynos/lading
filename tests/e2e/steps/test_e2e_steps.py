"""Step definitions for end-to-end lading CLI scenarios."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers, then, when

from lading.testing import toml_utils
from tests.e2e.helpers import git_helpers, workspace_builder
from tests.e2e.helpers.e2e_steps_helpers import (
    E2EExpectationError,
    extract_dependency_requirement,
    filter_records,
    find_staging_root,
    run_cli,
    stub_cargo_metadata,
)
from tests.e2e.helpers.e2e_steps_helpers import (
    _CmdMoxInvocation as CmdMoxInvocation,
)

if typ.TYPE_CHECKING:  # pragma: no cover
    import pytest
    from cmd_mox import CmdMox


def _validate_args_prefix(
    label: str,
    args: tuple[str, ...],
    expected_prefixes: tuple[tuple[str, ...], ...],
) -> None:
    """Validate that ``args`` begins with one of ``expected_prefixes``."""
    if expected_prefixes and not any(
        args[: len(prefix)] == prefix for prefix in expected_prefixes
    ):
        raise E2EExpectationError.args_prefix_mismatch(label, expected_prefixes, args)


def _validate_target_dir(label: str, args: tuple[str, ...]) -> None:
    """Validate that ``args`` contains ``--target-dir=...``."""
    if not any(argument.startswith("--target-dir=") for argument in args):
        raise E2EExpectationError.target_dir_missing(label, args)


def _create_recording_handler(
    label: str,
    invocation_records: list[tuple[str, tuple[str, ...], dict[str, str]]],
    expected_prefixes: tuple[tuple[str, ...], ...] = (),
    *,
    require_target_dir: bool = False,
) -> typ.Callable[[CmdMoxInvocation], tuple[str, str, int]]:
    """Create an invocation handler that validates and records cmd-mox calls."""

    def _handler(invocation: CmdMoxInvocation) -> tuple[str, str, int]:
        args = tuple(invocation.args)
        _validate_args_prefix(label, args, expected_prefixes)
        if require_target_dir:
            _validate_target_dir(label, args)
        invocation_records.append((label, args, dict(invocation.env)))
        return ("", "", 0)

    return _handler


@given(
    parsers.parse('a non-trivial workspace in a Git repository at version "{version}"'),
    target_fixture="e2e_state",
)
def given_nontrivial_workspace_in_git_repo(
    version: str,
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    e2e_workspace_with_git: tuple[workspace_builder.NonTrivialWorkspace, Path],
) -> dict[str, typ.Any]:
    """Create a non-trivial workspace fixture and stub cargo metadata."""
    if version != "0.1.0":
        raise E2EExpectationError.unsupported_fixture_version(version)
    e2e_workspace, e2e_git_repo = e2e_workspace_with_git
    monkeypatch.setenv("LADING_USE_CMD_MOX_STUB", "1")
    stub_cargo_metadata(cmd_mox, e2e_workspace)
    return {"workspace": e2e_workspace, "git_repo": e2e_git_repo}


@given(
    "cargo commands are stubbed for publish operations", target_fixture="publish_spies"
)
def given_cargo_commands_stubbed(
    cmd_mox: CmdMox,
    e2e_state: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Stub cargo pre-flight and publish loop commands; allow real git status."""
    cmd_mox.spy("git").passthrough()
    invocation_records: list[tuple[str, tuple[str, ...], dict[str, str]]] = []

    stub_configs: list[tuple[str, tuple[tuple[str, ...], ...], bool]] = [
        ("cargo::check", (("--workspace", "--all-targets"),), True),
        ("cargo::test", (("--workspace", "--all-targets"),), True),
        ("cargo::package", (), False),
        (
            "cargo::publish",
            (
                ("--dry-run",),
                ("--allow-dirty", "--dry-run"),
            ),
            False,
        ),
    ]

    for cmd_name, prefixes, requires_target in stub_configs:
        handler = _create_recording_handler(
            cmd_name,
            invocation_records,
            prefixes,
            require_target_dir=requires_target,
        )
        cmd_mox.stub(cmd_name).runs(handler)

    return {
        "records": invocation_records,
        "workspace": e2e_state["workspace"],
    }


@when(
    parsers.parse('I run lading bump "{version}" in the E2E workspace'),
    target_fixture="cli_run",
)
def when_run_lading_bump(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
    version: str,
) -> dict[str, typ.Any]:
    """Invoke `lading bump` against the E2E workspace and capture output."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    return run_cli(repo_root, workspace.root, "bump", version)


@when(
    "I run lading publish --forbid-dirty in the E2E workspace", target_fixture="cli_run"
)
def when_run_lading_publish(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Invoke `lading publish` (dry-run default) with `--forbid-dirty`."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    return run_cli(repo_root, workspace.root, "publish", "--forbid-dirty")


@when("I run lading publish in the E2E workspace", target_fixture="cli_run")
def when_run_lading_publish_allow_dirty(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Invoke `lading publish` using the default allow-dirty behaviour."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    return run_cli(repo_root, workspace.root, "publish")


@then("the command succeeds")
def then_command_succeeds(cli_run: dict[str, typ.Any]) -> None:
    """Assert the CLI exited successfully."""
    assert cli_run["returncode"] == 0, cli_run["stderr"]


@then('all workspace manifests are at version "1.0.0"')
def then_manifests_at_version(e2e_state: dict[str, typ.Any]) -> None:
    """Assert the workspace and member crate versions match the expected value."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    root_doc = toml_utils.load_manifest(workspace.root / "Cargo.toml")
    assert root_doc["workspace"]["package"]["version"] == "1.0.0"
    for name in workspace.crate_names:
        crate_doc = toml_utils.load_manifest(
            workspace.root / "crates" / name / "Cargo.toml"
        )
        assert crate_doc["package"]["version"] == "1.0.0"


@then('internal dependency versions are updated to "1.0.0"')
def then_internal_dependencies_updated(e2e_state: dict[str, typ.Any]) -> None:
    """Assert internal dependency version requirements reflect the new version."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    utils_doc = toml_utils.load_manifest(
        workspace.root / "crates" / "utils" / "Cargo.toml"
    )
    assert extract_dependency_requirement(utils_doc["dependencies"]["core"]) == "^1.0.0"
    assert (
        extract_dependency_requirement(utils_doc["dev-dependencies"]["core"])
        == "~1.0.0"
    )
    app_doc = toml_utils.load_manifest(workspace.root / "crates" / "app" / "Cargo.toml")
    assert extract_dependency_requirement(app_doc["dependencies"]["core"]) == "1.0.0"
    assert extract_dependency_requirement(app_doc["dependencies"]["utils"]) == "~1.0.0"
    assert (
        extract_dependency_requirement(app_doc["build-dependencies"]["core"]) == "1.0.0"
    )


@then('the workspace README contains version "1.0.0"')
def then_readme_contains_version(e2e_state: dict[str, typ.Any]) -> None:
    """Assert the workspace README TOML snippet reflects the bumped version."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    readme = (workspace.root / "README.md").read_text(encoding="utf-8")
    assert 'core = "1.0.0"' in readme
    assert 'utils = "1.0.0"' in readme
    assert 'app = "1.0.0"' in readme


@then("the Git working tree has uncommitted changes")
def then_git_dirty(e2e_state: dict[str, typ.Any]) -> None:
    """Assert `git status --porcelain` reports modifications in the workspace."""
    repo_root: Path = e2e_state["git_repo"]
    status = git_helpers.git_status_porcelain(repo_root)
    assert status.strip(), "expected a dirty git status"


@then("cargo preflight was run for the workspace")
def then_cargo_preflight_ran(publish_spies: dict[str, typ.Any]) -> None:
    """Assert publish executed cargo check and cargo test pre-flight commands."""
    check_calls = filter_records(publish_spies, "cargo::check")
    test_calls = filter_records(publish_spies, "cargo::test")
    assert check_calls, "expected at least one cargo::check preflight invocation"
    assert test_calls, "expected at least one cargo::test preflight invocation"


@then(parsers.parse('the publish order is "{expected}"'))
def then_publish_order(publish_spies: dict[str, typ.Any], expected: str) -> None:
    """Assert cargo package calls occur in the expected crate order."""
    expected_names = [name.strip() for name in expected.split(",") if name.strip()]
    package_calls = filter_records(publish_spies, "cargo::package")
    seen = []
    for _label, _args, env in package_calls:
        cwd = Path(env["PWD"])
        seen.append(cwd.name)
    assert seen == expected_names


@then("cargo package was invoked for each crate")
def then_cargo_package_invoked(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo package was invoked once per crate."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    package_calls = filter_records(publish_spies, "cargo::package")
    assert len(package_calls) == len(workspace.crate_names)
    called = {Path(env["PWD"]).name for _label, _args, env in package_calls}
    assert called == set(workspace.crate_names)


@then("cargo publish --dry-run was invoked for each crate")
def then_cargo_publish_invoked(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish --dry-run was invoked once per crate."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    publish_calls = filter_records(publish_spies, "cargo::publish")
    assert len(publish_calls) == len(workspace.crate_names)
    called = {Path(env["PWD"]).name for _label, _args, env in publish_calls}
    assert called == set(workspace.crate_names)


@then("cargo publish uses --allow-dirty in the default publish flow")
def then_cargo_publish_uses_allow_dirty(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish includes --allow-dirty when allow-dirty is enabled."""
    publish_calls = filter_records(publish_spies, "cargo::publish")
    assert publish_calls, "expected at least one cargo::publish invocation"
    seen_args = {args for _label, args, _env in publish_calls}
    assert seen_args == {("--allow-dirty", "--dry-run")}


@then("cargo publish omits --allow-dirty when forbid-dirty is set")
def then_cargo_publish_omits_allow_dirty(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish omits --allow-dirty when allow-dirty is disabled."""
    publish_calls = filter_records(publish_spies, "cargo::publish")
    assert publish_calls, "expected at least one cargo::publish invocation"
    seen_args = {args for _label, args, _env in publish_calls}
    assert seen_args == {("--dry-run",)}
    assert all("--allow-dirty" not in args for args in seen_args)


@then("the workspace README was staged for all crates")
def then_readme_staged(
    cli_run: dict[str, typ.Any],
    publish_spies: dict[str, typ.Any],
    staging_cleanup: typ.Callable[[Path], None],
) -> None:
    """Assert publish staging copied the workspace README into each crate."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    staging_root = find_staging_root(cli_run["stdout"])
    try:
        expected_paths = [
            staging_root / "crates" / name / "README.md"
            for name in workspace.crate_names
        ]
        for path in expected_paths:
            assert path.exists(), f"expected staged README: {path}"
    finally:
        staging_cleanup(staging_root)

    copied_lines = [
        line.strip()
        for line in cli_run["stdout"].splitlines()
        if line.strip().startswith("- crates/")
    ]
    assert len(copied_lines) == len(workspace.crate_names)
