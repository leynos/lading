"""BDD steps focused on the bump subcommand."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers, then, when

from lading.commands import bump_readme

from . import config_fixtures as _config_fixtures  # noqa: F401
from . import manifest_fixtures as _manifest_fixtures  # noqa: F401
from . import metadata_fixtures as _metadata_fixtures  # noqa: F401

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from cmd_mox import CmdMox

    from .test_common_steps import _run_cli  # noqa: F401


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


@when(
    parsers.parse("I invoke lading bump {version} with that workspace"),
    target_fixture="cli_run",
)
def when_invoke_lading_bump(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
) -> dict[str, typ.Any]:
    """Execute the bump CLI via ``python -m`` and capture the result."""
    from .test_common_steps import _run_cli

    return _run_cli(repo_root, workspace_directory, "bump", version)


@when(
    parsers.parse("I invoke lading bump {version} with that workspace using --dry-run"),
    target_fixture="cli_run",
)
def when_invoke_lading_bump_dry_run(
    version: str,
    workspace_directory: Path,
    repo_root: Path,
) -> dict[str, typ.Any]:
    """Execute the bump CLI in dry-run mode via ``python -m``."""
    from .test_common_steps import _run_cli

    return _run_cli(repo_root, workspace_directory, "bump", version, "--dry-run")


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
    normalised_expected = expected.replace(r"\"", '"')
    contents = doc_path.read_text(encoding="utf-8")
    assert normalised_expected in contents


@then(parsers.parse('the crate "{crate_name}" README contains "{expected}"'))
def then_crate_readme_contains(
    cli_run: dict[str, typ.Any], crate_name: str, expected: str
) -> None:
    """Assert that the adopted crate README contains ``expected``."""
    readme_path = cli_run["workspace"] / "crates" / crate_name / "README.md"
    normalised_expected = expected.replace(r"\"", '"')
    contents = readme_path.read_text(encoding="utf-8")
    assert normalised_expected in contents


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
