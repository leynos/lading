"""BDD steps focused on the bump subcommand."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers, then, when

from lading.commands import bump_readme

# Keep direct fixture imports for BDD collection even though conftest.py
# registers the modules as pytest plugins.
from . import config_fixtures as _config_fixtures  # ruff: ignore[unused-import]
from . import manifest_fixtures as _manifest_fixtures
from . import metadata_fixtures as _metadata_fixtures  # ruff: ignore[unused-import]

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from cmd_mox import CmdMox

    from .cli_run_types import CliRunResult
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
    """Track a nested standalone lockfile for discovered regeneration."""
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
    parsers.parse("I invoke lading bump {version} with that workspace"),
    target_fixture="cli_run",
)
def when_invoke_lading_bump(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
) -> CliRunResult:
    """Run the bump CLI and capture its output."""
    return _invoke_lading_bump(version, workspace_directory, repo_root)


@when(
    parsers.parse("I invoke lading bump {version} with that workspace using --dry-run"),
    target_fixture="cli_run",
)
def when_invoke_lading_bump_dry_run(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
) -> CliRunResult:
    """Run the bump CLI in dry-run mode and capture its output."""
    return _invoke_lading_bump(version, workspace_directory, repo_root, "--dry-run")


@then(parsers.parse('the bump command reports manifest updates for "{version}"'))
def then_command_reports_workspace(cli_run: dict[str, typ.Any], version: str) -> None:
    """Assert that the bump command reports the updated manifests."""
    assert cli_run["returncode"] == 0
    stdout = cli_run["stdout"]
    assert "Updated version to " in stdout
    assert version in stdout


@then(parsers.parse('the bump command reports no manifest changes for "{version}"'))
def then_command_reports_no_changes(
    cli_run: dict[str, typ.Any],
    version: str,
) -> None:
    """Assert that the bump command reports that no updates were required."""
    assert cli_run["returncode"] == 0
    stdout = cli_run["stdout"]
    assert "No manifest changes required" in stdout
    assert f"already {version}" in stdout


@then(parsers.parse('the bump command reports a dry-run plan for "{version}"'))
def then_command_reports_dry_run(
    cli_run: dict[str, typ.Any],
    version: str,
) -> None:
    """Assert that the bump command reports the dry-run summary."""
    assert cli_run["returncode"] == 0
    stdout = cli_run["stdout"]
    assert "Dry run;" in stdout
    assert f"would update version to {version}" in stdout


@then(
    parsers.parse('the bump command reports an invalid version error for "{version}"')
)
def then_bump_reports_invalid_version(
    cli_run: dict[str, typ.Any], version: str
) -> None:
    """Assert that invalid versions cause the command to fail with details."""
    assert cli_run["returncode"] == 1
    # Cyclopts renders argument-validation errors through its own console
    # (stdout), consistent with other cyclopts errors such as "Unknown command".
    stdout = cli_run["stdout"]
    assert f"Invalid version argument '{version}'" in stdout


@then(parsers.parse('the CLI output lists manifest paths "{first}" and "{second}"'))
def then_cli_output_lists_manifest_paths(
    cli_run: dict[str, typ.Any],
    first: str,
    second: str,
) -> None:
    """Assert that the CLI output lists the expected manifest paths."""
    assert cli_run["returncode"] == 0
    expected_lines = [first, second]
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    manifest_lines = [
        line
        for line in stdout_lines
        if line.startswith("- ") and line.endswith("Cargo.toml")
    ]
    assert manifest_lines == expected_lines


@then(parsers.parse('the CLI output lists manifest path "{expected}"'))
def then_cli_output_lists_one_manifest_path(
    cli_run: dict[str, typ.Any], expected: str
) -> None:
    """Assert that one configured manifest appears in the report."""
    assert cli_run["returncode"] == 0, cli_run["stdout"]
    assert (
        expected in [line.strip() for line in cli_run["stdout"].splitlines()]
    ), f"{expected!r} missing from CLI output:\n{cli_run['stdout']}"


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
        invocation for invocation in cmd_mox.journal
        if invocation.command == "cargo::update"
    ]
    assert updates == [], f"unexpected Cargo updates: {updates!r}"


@then(parsers.parse('the CLI output lists documentation path "{expected}"'))
def then_cli_output_lists_documentation_path(
    cli_run: dict[str, typ.Any], expected: str
) -> None:
    """Assert that the CLI output includes ``expected`` as a documentation line."""
    assert cli_run["returncode"] == 0
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert expected in stdout_lines


@then(parsers.parse('the CLI output lists README path "{expected}"'))
def then_cli_output_lists_readme_path(
    cli_run: dict[str, typ.Any], expected: str
) -> None:
    """Assert that the CLI output includes ``expected`` as a README line."""
    assert cli_run["returncode"] == 0
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert expected in stdout_lines


@then(parsers.parse('the CLI output lists lockfile path "{expected}"'))
def then_cli_output_lists_lockfile_path(
    cli_run: dict[str, typ.Any], expected: str
) -> None:
    """Assert that the CLI output includes ``expected`` as a lockfile line."""
    assert cli_run["returncode"] == 0
    stdout_lines = [line.strip() for line in cli_run["stdout"].splitlines()]
    assert expected in stdout_lines


@then("the bump command refreshed tracked lockfiles")
def then_bump_refreshed_lockfiles(cli_run: dict[str, typ.Any]) -> None:
    """Assert the live bump lockfile scenario completed successfully."""
    assert cli_run["returncode"] == 0
    output = f"{cli_run['stdout']}\n{cli_run['stderr']}"
    assert "cargo update --workspace" in output


@then("the bump command refreshed workspace and nested tracked lockfiles")
def then_bump_refreshed_workspace_and_nested_lockfiles(
    cli_run: CliRunResult,
    cmd_mox: CmdMox,
    workspace_directory: Path,
) -> None:
    """Assert Cargo refreshed the root and discovered nested lockfiles."""
    assert cli_run["returncode"] == 0, (
        f"stdout:\n{cli_run['stdout']}\nstderr:\n{cli_run['stderr']}"
    )
    update_args = [
        tuple(invocation.args)
        for invocation in cmd_mox.journal
        if invocation.command == "cargo::update"
    ]
    expected_args = [
        (
            "--workspace",
            "--manifest-path",
            str(workspace_directory / "Cargo.toml"),
        ),
        (
            "--workspace",
            "--manifest-path",
            str(workspace_directory / "fixtures/minimal/Cargo.toml"),
        ),
    ]
    assert update_args == expected_args, (
        f"expected distinct root and nested Cargo updates; got {update_args!r}"
    )


@then("the bump command did not refresh tracked lockfiles")
def then_bump_did_not_refresh_lockfiles(cli_run: dict[str, typ.Any]) -> None:
    """Assert the dry-run lockfile scenario completed without refresh."""
    assert cli_run["returncode"] == 0
    output = f"{cli_run['stdout']}\n{cli_run['stderr']}"
    assert "cargo::update" not in output
    assert "cargo update --workspace" not in output


@then(parsers.parse('the documentation file "{relative_path}" contains "{expected}"'))
def then_documentation_contains(
    cli_run: dict[str, typ.Any], relative_path: str, expected: str
) -> None:
    """Assert that ``expected`` appears in the specified documentation file."""
    doc_path = cli_run["workspace"] / relative_path
    normalized_expected = expected.replace(r"\"", '"')
    contents = doc_path.read_text(encoding="utf-8")
    assert normalized_expected in contents


@then(parsers.parse('the crate "{crate_name}" README contains "{expected}"'))
def then_crate_readme_contains(
    cli_run: dict[str, typ.Any], crate_name: str, expected: str
) -> None:
    """Assert that the adopted crate README contains ``expected``."""
    readme_path = cli_run["workspace"] / "crates" / crate_name / "README.md"
    normalized_expected = expected.replace(r"\"", '"')
    contents = readme_path.read_text(encoding="utf-8")
    assert normalized_expected in contents


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


@given(parsers.parse('a nested lockfile manifest is configured at "{manifest}"'))
def given_nested_lockfile_manifest(
    cmd_mox: CmdMox,
    workspace_directory: Path,
    manifest: str,
) -> None:
    """Configure ``bump.lockfile_manifests`` and stub its cargo rebuild."""
    from pathlib import Path as _Path

    from lading import config as config_module
    from lading.testing import toml_utils

    config_path = workspace_directory / config_module.CONFIG_FILENAME
    document = toml_utils.load_or_create_document(config_path)
    bump_table = toml_utils.ensure_table(document, "bump")
    manifests = toml_utils.ensure_array_field(bump_table, "lockfile_manifests")
    toml_utils.append_if_absent(manifests, manifest)
    config_path.write_text(document.as_string(), encoding="utf-8")

    nested_manifest = workspace_directory / _Path(manifest)
    nested_manifest.parent.mkdir(parents=True, exist_ok=True)
    nested_manifest.write_text(
        '[package]\nname = "nested"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (nested_manifest.parent / "Cargo.lock").write_text(
        "# nested lock\n", encoding="utf-8"
    )
    cmd_mox.stub("cargo::update").with_args(
        "--workspace", "--manifest-path", str(nested_manifest.resolve())
    ).returns(exit_code=0, stdout="cargo update --workspace\n", stderr="")


def _invoke_lading_bump(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
    *options: str,
) -> CliRunResult:
    """Run the bump CLI for ``version`` and capture the run result."""
    from .test_common_steps import _run_cli

    return _run_cli(repo_root, workspace_directory, "bump", version, *options)
