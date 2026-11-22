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
def then_bump_refreshed_lockfiles(
    cli_run: CliRunResult,
    cmd_mox: CmdMox,
    workspace_directory: Path,
) -> None:
    """Assert the live lockfile scenario invoked Cargo for the root manifest."""
    assert cli_run["returncode"] == 0, (
        f"the live lockfile bump must succeed; stderr:\n{cli_run['stderr']}"
    )
    update_args = [
        tuple(invocation.args)
        for invocation in cmd_mox.journal
        if invocation.command == "cargo::update"
    ]
    expected_root_update = (
        "--workspace",
        "--manifest-path",
        str((workspace_directory / "Cargo.toml").resolve()),
    )
    assert expected_root_update in update_args, (
        f"the bump must invoke Cargo update for the root lockfile; got {update_args!r}"
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
def then_bump_did_not_refresh_lockfiles(
    cli_run: dict[str, typ.Any],
    cmd_mox: CmdMox,
) -> None:
    """Assert the dry-run lockfile scenario completed without refresh."""
    assert cli_run["returncode"] == 0, (
        f"the dry-run lockfile bump must succeed; stderr:\n{cli_run['stderr']}"
    )
    updates = [
        invocation
        for invocation in cmd_mox.journal
        if invocation.command == "cargo::update"
    ]
    assert not updates, f"dry run must not invoke Cargo update; got {updates!r}"
