"""BDD steps focused on the bump subcommand."""

import typing as typ
from pathlib import Path

import pytest
from cmd_mox import CmdMox
from pytest_bdd import given, parsers, then, when

from lading.commands import bump_readme

# Keep direct fixture imports for BDD collection even though conftest.py
# registers the modules as pytest plugins.
from . import config_fixtures as _config_fixtures  # ruff: ignore[unused-import]
from . import manifest_fixtures as _manifest_fixtures
from . import (
    metadata_fixtures as _metadata_fixtures,  # ruff: ignore[unused-import] - direct import keeps the metadata step module collected
)
from .cli_run_types import CliRunResult

if typ.TYPE_CHECKING:

    from .test_common_steps import (
        _run_cli,  # ruff: ignore[unused-import] - imported only for type checking
    )


@given("the workspace has tracked Cargo.lock files")
def given_workspace_has_tracked_lockfiles(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    workspace_directory: Path,
) -> None:
    """Stub tracked Cargo.lock discovery and refresh commands for bump."""
    from tests.helpers.workspace_helpers import install_cargo_stub

    install_cargo_stub(cmd_mox, monkeypatch)
    (workspace_directory / "Cargo.lock").write_text("# root lock\n", encoding="utf-8")
    cmd_mox.stub("git").with_args("ls-files", "**/Cargo.lock", "Cargo.lock").returns(
        exit_code=0, stdout="Cargo.lock\n", stderr=""
    )
    cmd_mox.stub("cargo::update").with_args(
        "--workspace", "--manifest-path", str(workspace_directory / "Cargo.toml")
    ).returns(exit_code=0, stdout="cargo update --workspace\n", stderr="")


@given("the workspace has a tracked nested Cargo.lock file")
def given_workspace_has_nested_tracked_lockfile(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    workspace_directory: Path,
) -> None:
    """Track a nested standalone package lockfile alongside the root lockfile.

    The nested package is not listed in ``bump.lockfile_manifests``; bump must
    discover it from the git index and refresh it anyway.

    Parameters
    ----------
    cmd_mox : CmdMox
        Command-double fixture used to stub Git, Cargo, and metadata commands.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to install the Cargo command shim for the scenario.
    workspace_directory : Path
        Temporary workspace containing the root and nested lockfiles.

    """
    from tests.helpers.workspace_helpers import install_cargo_stub

    install_cargo_stub(cmd_mox, monkeypatch)
    (workspace_directory / "Cargo.lock").write_text("# root lock\n", encoding="utf-8")
    nested_dir = workspace_directory / "fixtures" / "minimal"
    nested_dir.mkdir(parents=True)
    (nested_dir / "Cargo.toml").write_text(
        '[package]\nname = "minimal"\nversion = "0.1.0"\n\n[workspace]\n',
        encoding="utf-8",
    )
    (nested_dir / "Cargo.lock").write_text("# nested lock\n", encoding="utf-8")
    cmd_mox.stub("git").with_args("ls-files", "**/Cargo.lock", "Cargo.lock").returns(
        exit_code=0,
        stdout="Cargo.lock\nfixtures/minimal/Cargo.lock\n",
        stderr="",
    )
    # Stubs dispatch by command name alone, so one argless registration covers
    # both invocations. The scenario's Then step verifies their exact arguments.
    cmd_mox.stub("cargo::update").returns(
        exit_code=0, stdout="cargo update --workspace\n", stderr=""
    )


@when(
    parsers.re(
        r"I invoke lading bump (?P<version>\S+) with that workspace"
        r"(?P<mode> using --dry-run)?"
    ),
    target_fixture="cli_run",
)
def when_invoke_lading_bump(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
    mode: str | None,
) -> CliRunResult:
    """Execute the bump CLI via ``python -m`` and capture the result.

    Parameters
    ----------
    version : str
        The version argument passed to the ``bump`` subcommand.
    workspace_directory : Path
        The workspace root supplied via ``--workspace-root``.
    repo_root : Path
        The repository root used as the subprocess working directory.
    mode : str or None
        The optional ``using --dry-run`` suffix captured from the step text.

    Returns
    -------
    CliRunResult
        The captured CLI run details (return code, stdout, stderr, and the
        resolved workspace path).
    """
    extra = ("--dry-run",) if mode else ()
    return _invoke_lading_bump(version, workspace_directory, repo_root, *extra)


@then(parsers.parse('the bump command reports manifest updates for "{version}"'))
def then_command_reports_workspace(cli_run: dict[str, typ.Any], version: str) -> None:
    """Assert that the bump command reports the updated manifests."""
    assert cli_run["returncode"] == 0, (
        f"bump must succeed for version {version!r}; stderr:\n{cli_run['stderr']}"
    )
    stdout = cli_run["stdout"]
    assert "Updated version to " in stdout, (
        "bump must emit the 'Updated version to' header when manifests change"
    )
    assert version in stdout, (
        f"bump output must report the requested version {version!r}"
    )


@then(parsers.parse('the bump command reports no manifest changes for "{version}"'))
def then_command_reports_no_changes(
    cli_run: dict[str, typ.Any],
    version: str,
) -> None:
    """Assert that the bump command reports that no updates were required."""
    assert cli_run["returncode"] == 0, (
        f"a no-op bump to {version!r} must still succeed; "
        f"stderr:\n{cli_run['stderr']}"
    )
    stdout = cli_run["stdout"]
    assert "No manifest changes required" in stdout, (
        "bump must emit the no-change notice when all versions already match"
    )
    assert f"already {version}" in stdout, (
        f"the no-change notice must name {version!r} as the current version"
    )


@then(parsers.parse('the bump command reports a dry-run plan for "{version}"'))
def then_command_reports_dry_run(
    cli_run: dict[str, typ.Any],
    version: str,
) -> None:
    """Assert that the bump command reports the dry-run summary."""
    assert cli_run["returncode"] == 0, (
        f"a dry-run bump to {version!r} must succeed; stderr:\n{cli_run['stderr']}"
    )
    stdout = cli_run["stdout"]
    assert "Dry run;" in stdout, (
        "a dry run must label its output with the 'Dry run;' prefix"
    )
    assert f"would update version to {version}" in stdout, (
        f"the dry-run plan must state it would update version to {version!r}"
    )


@then(
    parsers.parse('the bump command reports an invalid version error for "{version}"')
)
def then_bump_reports_invalid_version(
    cli_run: dict[str, typ.Any], version: str
) -> None:
    """Assert that invalid versions cause the command to fail with details."""
    assert cli_run["returncode"] == 1, (
        f"an invalid version {version!r} must fail the command with exit code 1, "
        f"but exited with {cli_run['returncode']}"
    )
    # Cyclopts renders argument-validation errors through its own console
    # (stdout), consistent with other cyclopts errors such as "Unknown command".
    stdout = cli_run["stdout"]
    assert f"Invalid version argument '{version}'" in stdout, (
        f"the failure must name {version!r} as an invalid version argument"
    )


@then(parsers.parse('the CLI output lists manifest paths "{first}" and "{second}"'))
def then_cli_output_lists_manifest_paths(
    cli_run: dict[str, typ.Any],
    first: str,
    second: str,
) -> None:
    """Assert that the CLI output lists the expected manifest paths."""
    assert cli_run["returncode"] == 0, (
        "listing manifest paths requires a successful bump run; "
        f"stderr:\n{cli_run['stderr']}"
    )
    expected_lines = [first, second]
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    manifest_lines = [
        line
        for line in stdout_lines
        if line.startswith("- ") and line.endswith("Cargo.toml")
    ]
    assert manifest_lines == expected_lines, (
        "the output must list the changed manifests as bullet lines in order"
    )


@then(parsers.parse('the CLI output lists manifest path "{expected}"'))
def then_cli_output_lists_one_manifest_path(
    cli_run: dict[str, typ.Any], expected: str
) -> None:
    """Assert one configured manifest appears in the bump report."""
    assert cli_run["returncode"] == 0, (
        "listing a manifest requires a successful bump run; "
        f"stderr:\n{cli_run['stderr']}"
    )
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert expected in stdout_lines, (
        f"the output must list configured manifest {expected!r}"
    )


@given(
    "cargo metadata describes the rstest-bdd workspace and published fixture",
    target_fixture="published_fixture_original",
)
def given_published_fixture_workspace(
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    workspace_directory: Path,
) -> bytes:
    """Create the beta4 workspace and configured standalone fixture."""
    return _manifest_fixtures._prepare_published_gpui_e2e_fixture(
        cmd_mox, monkeypatch, workspace_directory
    )


@then("the published fixture manifest references only 0.6.0")
def then_published_fixture_versions_updated(cli_run: CliRunResult) -> None:
    """Assert dependency and staged-path prerelease values were rewritten."""
    fixture = cli_run["workspace"] / "tests/fixtures/published-gpui-e2e/Cargo.toml"
    contents = fixture.read_text(encoding="utf-8")
    assert "0.6.0-beta4" not in contents, f"prerelease remains:\n{contents}"
    assert contents.count("0.6.0") == 6, f"unexpected rewrite count:\n{contents}"


@then("the published fixture manifest remains unchanged")
def then_published_fixture_unchanged(
    cli_run: CliRunResult,
    published_fixture_original: bytes,
) -> None:
    """Assert dry-run preserved the fixture's exact bytes."""
    fixture = cli_run["workspace"] / "tests/fixtures/published-gpui-e2e/Cargo.toml"
    actual = fixture.read_bytes()
    assert actual == published_fixture_original, f"fixture changed: {actual!r}"


@then("the bump made no Cargo lockfile update invocations")
def then_no_cargo_lockfile_updates(cmd_mox: CmdMox) -> None:
    """Assert dry-run projection did not invoke Cargo lockfile updates."""
    updates = [
        invocation
        for invocation in cmd_mox.journal
        if invocation.command == "cargo::update"
    ]
    assert not updates, f"unexpected Cargo updates: {updates!r}"


_LISTED_PATH_LABELS = {
    "documentation": "changed documentation file",
    "README": "transposed README",
    "lockfile": "refreshed lockfile",
}


@then(
    parsers.re(
        r'the CLI output lists (?P<kind>documentation|README|lockfile) path'
        r' "(?P<expected>[^"]+)"'
    )
)
def then_cli_output_lists_path(
    cli_run: dict[str, typ.Any], kind: str, expected: str
) -> None:
    """Assert that the CLI output includes ``expected`` as a ``kind`` path line."""
    assert cli_run["returncode"] == 0, (
        f"listing a {kind} path requires a successful bump run; "
        f"stderr:\n{cli_run['stderr']}"
    )
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert expected in stdout_lines, (
        f"the output must acknowledge the {_LISTED_PATH_LABELS[kind]} {expected!r}"
    )


@then(parsers.parse('the documentation file "{relative_path}" contains "{expected}"'))
def then_documentation_contains(
    cli_run: dict[str, typ.Any], relative_path: str, expected: str
) -> None:
    """Assert that ``expected`` appears in the specified documentation file."""
    doc_path = cli_run["workspace"] / relative_path
    normalized_expected = expected.replace(r"\"", '"')
    contents = doc_path.read_text(encoding="utf-8")
    assert normalized_expected in contents, (
        f"the documentation file {relative_path!r} must contain {expected!r} "
        "after the bump"
    )


@then(parsers.parse('the crate "{crate_name}" README contains "{expected}"'))
def then_crate_readme_contains(
    cli_run: dict[str, typ.Any], crate_name: str, expected: str
) -> None:
    """Assert that the adopted crate README contains ``expected``."""
    readme_path = cli_run["workspace"] / "crates" / crate_name / "README.md"
    normalized_expected = expected.replace(r"\"", '"')
    contents = readme_path.read_text(encoding="utf-8")
    assert normalized_expected in contents, (
        f"the {crate_name!r} crate README must contain {expected!r} "
        "after the bump"
    )


@given(parsers.parse('the workspace README contains a relative link to "{target}"'))
def given_workspace_readme_relative_link(
    workspace_directory: Path,
    target: str,
) -> None:
    """Write a workspace README containing a relative Markdown link."""
    readme_path = workspace_directory / "README.md"
    readme_path.write_text(
        f"# Workspace README\n\nSee [migration guide]({target}).\n",
        encoding="utf-8",
    )


@given(
    parsers.parse(
        'the crate "{crate_name}" README already matches the workspace README'
    )
)
def given_crate_readme_matches_workspace(
    workspace_directory: Path,
    crate_name: str,
) -> None:
    """Write the already-transposed README expected for ``crate_name``."""
    workspace_readme = workspace_directory / "README.md"
    crate_root = workspace_directory / "crates" / crate_name
    crate_readme = crate_root / "README.md"
    rewritten_text, _ = bump_readme.rewrite_relative_links(
        workspace_readme.read_text(encoding="utf-8"),
        bump_readme.compute_link_prefix(crate_root.relative_to(workspace_directory)),
    )
    crate_readme.write_text(rewritten_text, encoding="utf-8")


def _invoke_lading_bump(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
    *options: str,
) -> CliRunResult:
    """Run the bump CLI for ``version`` and capture the run result."""
    from .test_common_steps import _run_cli

    return _run_cli(repo_root, workspace_directory, "bump", version, *options)
