"""Step definitions for end-to-end lading CLI scenarios."""

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest
from cmd_mox import CmdMox, Invocation
from pytest_bdd import given, parsers, then, when

from lading import config as config_module
from lading.testing import toml_utils
from tests.e2e.helpers import git_helpers, workspace_builder
from tests.e2e.helpers.e2e_steps_helpers import (
    E2EExpectationError,
    _CliRunResult,
    extract_dependency_requirement,
    run_lading_in_e2e_workspace,
    stub_cargo_metadata,
)


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
    """Validate that ``args`` contains a ``--target-dir=...`` argument."""
    if not any(argument.startswith("--target-dir=") for argument in args):
        raise E2EExpectationError.target_dir_missing(label, args)


def _create_recording_handler(
    label: str,
    invocation_records: list[tuple[str, tuple[str, ...], dict[str, str]]],
    expected_prefixes: tuple[tuple[str, ...], ...] = (),
    *,
    require_target_dir: bool = False,
) -> cabc.Callable[[Invocation], tuple[str, str, int]]:
    """Create an invocation handler that validates and records cmd-mox calls."""

    def _handler(invocation: Invocation) -> tuple[str, str, int]:
        """Record a validated invocation and return a silent success."""
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
    """Create a non-trivial workspace fixture and stub cargo metadata.

    Parameters
    ----------
    version : str
        Fixture version parsed from the scenario step; must be ``"0.1.0"``.
    cmd_mox : CmdMox
        Command mocker used to stub cargo and spy on git.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to set the cmd-mox stub environment variable.
    e2e_workspace_with_git : tuple[workspace_builder.NonTrivialWorkspace, Path]
        The built workspace paired with its Git repository root.

    Returns
    -------
    dict[str, typ.Any]
        State mapping with the built ``workspace`` and its ``git_repo`` path.

    Raises
    ------
    E2EExpectationError
        Constructed by
        ``E2EExpectationError.unsupported_fixture_version(version)`` when the
        fixture version is unsupported.

    Examples
    --------
    Bound to the Gherkin ``given`` step and invoked by pytest-bdd; the
    returned mapping seeds later steps as the ``e2e_state`` fixture::

        >>> state = given_nontrivial_workspace_in_git_repo(  # doctest: +SKIP
        ...     "0.1.0", cmd_mox, monkeypatch, e2e_workspace_with_git
        ... )
        >>> sorted(state)  # doctest: +SKIP
        ['git_repo', 'workspace']
    """
    if version != "0.1.0":
        raise E2EExpectationError.unsupported_fixture_version(version)
    e2e_workspace, e2e_git_repo = e2e_workspace_with_git
    monkeypatch.setenv("LADING_USE_CMD_MOX_STUB", "1")
    cmd_mox.spy("git").passthrough()
    stub_cargo_metadata(cmd_mox, e2e_workspace)
    return {"workspace": e2e_workspace, "git_repo": e2e_git_repo}


@given(
    "cargo commands are stubbed for publish operations", target_fixture="publish_spies"
)
def given_cargo_commands_stubbed(
    cmd_mox: CmdMox,
    e2e_state: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Stub cargo pre-flight and publish loop commands; allow real git status.

    Parameters
    ----------
    cmd_mox : CmdMox
        Command mocker used to stub cargo commands and allow real git status.
    e2e_state : dict[str, typ.Any]
        Shared step state carrying the target ``workspace``.

    Returns
    -------
    dict[str, typ.Any]
        State mapping with the recorded ``records`` list and the ``workspace``.

    Examples
    --------
    Bound to the Gherkin ``given`` step and invoked by pytest-bdd; the
    returned mapping is exposed as the ``publish_spies`` fixture::

        >>> state = given_cargo_commands_stubbed(  # doctest: +SKIP
        ...     cmd_mox, e2e_state
        ... )
        >>> sorted(state)  # doctest: +SKIP
        ['records', 'workspace']
    """
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
) -> _CliRunResult:
    """Invoke `lading bump` against the E2E workspace and capture output.

    Parameters
    ----------
    repo_root : Path
        Repository root from which the CLI module is executed.
    e2e_state : dict[str, typ.Any]
        Shared step state carrying the target ``workspace``.
    version : str
        Version string parsed from the scenario step.

    Returns
    -------
    _CliRunResult
        The captured CLI result mapping with the executed ``command``,
        ``returncode``, ``stdout``, ``stderr``, and ``workspace_root``.

    Examples
    --------
    Bound to the Gherkin ``when`` step and invoked by pytest-bdd; the
    returned mapping is exposed as the ``cli_run`` fixture::

        >>> result = when_run_lading_bump(  # doctest: +SKIP
        ...     repo_root, e2e_state, "1.0.0"
        ... )
        >>> result["returncode"]  # doctest: +SKIP
        0
    """
    return run_lading_in_e2e_workspace(repo_root, e2e_state, "bump", version)


@when("I commit the E2E workspace changes")
def when_commit_e2e_workspace_changes(e2e_state: dict[str, typ.Any]) -> None:
    """Commit bump output so publish can enforce a clean working tree."""
    repo_root: Path = e2e_state["git_repo"]
    git_helpers.git_add_all(repo_root)
    git_helpers.git_commit(repo_root, "Record bumped workspace")


@then("the command succeeds")
def then_command_succeeds(cli_run: _CliRunResult) -> None:
    """Assert the CLI exited successfully."""
    assert cli_run["returncode"] == 0, cli_run["stderr"]


@then('all workspace manifests are at version "1.0.0"')
def then_manifests_at_version(e2e_state: dict[str, typ.Any]) -> None:
    """Assert the workspace and member crate versions match the expected value."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    root_doc = toml_utils.load_manifest(workspace.root / "Cargo.toml")
    assert root_doc["workspace"]["package"]["version"] == "1.0.0", root_doc
    for name in workspace.crate_names:
        crate_doc = toml_utils.load_manifest(
            workspace.root / "crates" / name / "Cargo.toml"
        )
        assert crate_doc["package"]["version"] == "1.0.0", crate_doc


@then('internal dependency versions are updated to "1.0.0"')
def then_internal_dependencies_updated(e2e_state: dict[str, typ.Any]) -> None:
    """Assert internal dependency version requirements reflect the new version."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    utils_doc = toml_utils.load_manifest(
        workspace.root / "crates" / "utils" / "Cargo.toml"
    )
    assert (
        extract_dependency_requirement(utils_doc["dependencies"]["core"]) == "^1.0.0"
    ), utils_doc
    assert (
        extract_dependency_requirement(utils_doc["dev-dependencies"]["core"])
        == "~1.0.0"
    ), utils_doc
    app_doc = toml_utils.load_manifest(workspace.root / "crates" / "app" / "Cargo.toml")
    assert extract_dependency_requirement(app_doc["dependencies"]["core"]) == "1.0.0", (
        app_doc
    )
    assert (
        extract_dependency_requirement(app_doc["dependencies"]["utils"]) == "~1.0.0"
    ), app_doc
    assert (
        extract_dependency_requirement(app_doc["build-dependencies"]["core"]) == "1.0.0"
    ), app_doc


@then('the workspace README contains version "1.0.0"')
def then_readme_contains_version(e2e_state: dict[str, typ.Any]) -> None:
    """Assert the workspace README TOML snippets reflect the bumped version."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    readme = (workspace.root / "README.md").read_text(encoding="utf-8")
    expected = workspace_builder.workspace_readme(workspace.crate_names, "1.0.0")
    assert readme == expected, f"expected every fence rewritten: {readme!r}"


@then("the Git working tree has uncommitted changes")
def then_git_dirty(e2e_state: dict[str, typ.Any]) -> None:
    """Assert `git status --porcelain` reports modifications in the workspace."""
    repo_root: Path = e2e_state["git_repo"]
    status = git_helpers.git_status_porcelain(repo_root)
    assert status.strip(), "expected a dirty git status"


@then("the workspace README was adopted for all crates")
def then_readme_adopted_for_all_crates(e2e_state: dict[str, typ.Any]) -> None:
    """Assert bump transposed the workspace README into each crate."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    source_readme = workspace.root / "README.md"
    source_text = source_readme.read_text(encoding="utf-8")
    expected_text = source_text.replace(
        "docs/v1-0-0-migration-guide.md",
        "../../docs/v1-0-0-migration-guide.md",
    )
    for name in workspace.crate_names:
        crate_readme = workspace.root / "crates" / name / "README.md"
        assert crate_readme.exists(), f"expected adopted README: {crate_readme}"
        assert crate_readme.read_text(encoding="utf-8") == expected_text, crate_readme


@given("the workspace rebuilds a nested standalone lockfile during bump")
def given_workspace_rebuilds_nested_lockfile(
    cmd_mox: CmdMox,
    e2e_state: dict[str, typ.Any],
) -> None:
    """Configure a nested standalone workspace and a lockfile-writing cargo stub.

    The nested package is deliberately not a member of the outer workspace.
    Cargo then reports the nested directory as its own workspace root, so the
    bump plan must name the nested ``Cargo.lock`` rather than the outer one.
    """
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    nested_root = workspace.root / "fixtures" / "nested"
    nested_root.mkdir(parents=True)
    (nested_root / "Cargo.toml").write_text(
        '[package]\nname = "nested"\nversion = "0.1.0"\n\n[workspace]\n',
        encoding="utf-8",
    )
    config_path = workspace.root / config_module.CONFIG_FILENAME
    document = toml_utils.load_or_create_document(config_path)
    bump_table = toml_utils.ensure_table(document, "bump")
    manifests = toml_utils.ensure_array_field(bump_table, "lockfile_manifests")
    toml_utils.append_if_absent(manifests, "fixtures/nested/Cargo.toml")
    config_path.write_text(document.as_string(), encoding="utf-8")

    stale_marker = "# stale lockfile\n"
    (workspace.root / "Cargo.lock").write_text(stale_marker, encoding="utf-8")
    nested_lockfile = nested_root / "Cargo.lock"
    nested_lockfile.write_text(stale_marker, encoding="utf-8")

    def regenerate(invocation: Invocation) -> tuple[str, str, int]:
        """Mimic cargo update by rewriting the targeted Cargo.lock."""
        argv = list(invocation.args)
        manifest = Path(argv[argv.index("--manifest-path") + 1])
        (manifest.parent / "Cargo.lock").write_text(
            "# regenerated by cargo update\n", encoding="utf-8"
        )
        return ("", "", 0)

    cmd_mox.stub("cargo::update").with_matching_args(
        lambda *_args: True,
    ).runs(regenerate)
    e2e_state["regenerated_marker"] = "# regenerated by cargo update\n"


@then("the CLI output lists the regenerated lockfiles")
def then_cli_lists_regenerated_lockfiles(cli_run: _CliRunResult) -> None:
    """Assert the bump output reports the root and nested lockfiles."""
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert "- Cargo.lock (lockfile)" in stdout_lines, stdout_lines
    assert "- fixtures/nested/Cargo.lock (lockfile)" in stdout_lines, stdout_lines


@then("the workspace Cargo.lock files are updated")
def then_workspace_lockfiles_updated(
    e2e_state: dict[str, typ.Any],
) -> None:
    """Assert the lockfiles were rewritten by the cargo update invocations."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    marker = e2e_state["regenerated_marker"]
    for lockfile in (
        workspace.root / "Cargo.lock",
        workspace.root / "fixtures" / "nested" / "Cargo.lock",
    ):
        assert lockfile.read_text(encoding="utf-8") == marker, lockfile
