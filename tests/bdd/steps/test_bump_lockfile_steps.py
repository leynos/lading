"""BDD steps for tracked bump lockfile behaviour."""

import typing as typ
from pathlib import Path

from cmd_mox import CmdMox
from pytest_bdd import given, parsers, then

from .cli_run_types import CliRunResult

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


@then("the bump command refreshed tracked lockfiles")
def then_bump_refreshed_lockfiles(cli_run: dict[str, typ.Any]) -> None:
    """Assert the live bump lockfile scenario completed successfully."""
    assert cli_run["returncode"] == 0, (
        f"the live lockfile bump must succeed; stderr:\n{cli_run['stderr']}"
    )
    output = f"{cli_run['stdout']}\n{cli_run['stderr']}"
    assert "cargo update --workspace" in output, (
        "the bump must refresh the tracked lockfiles via cargo update --workspace"
    )


@then("the bump command refreshed workspace and nested tracked lockfiles")
def then_bump_refreshed_workspace_and_nested_lockfiles(
    cli_run: CliRunResult,
    cmd_mox: CmdMox,
    workspace_directory: Path,
) -> None:
    """Assert Cargo refreshed the root and discovered nested lockfiles.

    Parameters
    ----------
    cli_run : CliRunResult
        Captured result of the completed ``lading bump`` invocation.
    cmd_mox : CmdMox
        Command-double fixture whose journal records Cargo update calls.
    workspace_directory : Path
        Temporary workspace used to derive the expected manifest paths.

    """
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
    assert cli_run["returncode"] == 0, (
        f"the dry-run lockfile bump must succeed; stderr:\n{cli_run['stderr']}"
    )
    output = f"{cli_run['stdout']}\n{cli_run['stderr']}"
    assert "cargo::update" not in output, (
        "a dry run must not invoke the cargo update shim"
    )
    assert "cargo update --workspace" not in output, (
        "a dry run must not refresh lockfiles"
    )
