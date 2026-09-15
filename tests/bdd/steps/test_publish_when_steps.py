"""When steps for publish BDD scenarios."""

from __future__ import annotations

import collections.abc as cabc
import shlex
import shutil
import typing as typ

import pytest
from pytest_bdd import parsers, when

from .test_publish_infrastructure import (
    PreflightTestContext,
    _CommandResponse,
    _invoke_publish_with_options,
    _is_cargo_publish_command,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    from .cli_run_types import CliRunResult


@when("I invoke lading publish with that workspace", target_fixture="cli_run")
def when_invoke_lading_publish(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
) -> CliRunResult:
    """Run the publish CLI against the staged workspace and capture the result."""
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(repo_root, workspace_directory, stub_config)


@pytest.fixture
def _staging_kept_under_a_removable_directory(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> cabc.Iterator[None]:
    """Point the publish subprocess's ``TMPDIR`` at a directory this removes.

    Retaining a staged copy makes its removal the caller's responsibility, so
    a scenario that asks for one removes it here rather than leaving it on the
    host (issue #269). The directory comes from the factory rather than
    ``tmp_path`` because ``tmp_path`` is the workspace root in these scenarios,
    and staging refuses to nest inside the workspace it is copying.

    Yields
    ------
    None
        Control, while ``TMPDIR`` is redirected.
    """
    root = tmp_path_factory.mktemp("retained-staging")
    monkeypatch.setenv("TMPDIR", str(root))
    yield
    shutil.rmtree(root, ignore_errors=True)


@when(
    "I invoke lading publish with that workspace, retaining the staged copy",
    target_fixture="cli_run",
)
def when_invoke_lading_publish_retaining_the_staged_copy(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
    _staging_kept_under_a_removable_directory: None,
) -> CliRunResult:
    """Run the publish CLI with ``--keep-staging`` and capture the result.

    These scenarios assert on the staged manifest, which a publish now removes
    when it ends, so the copy has to be retained deliberately.

    Returns
    -------
    CliRunResult
        The captured exit status, stdout and stderr of the publish run.
    """
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root, workspace_directory, stub_config, "--keep-staging"
    )


@when(parsers.parse('I run "{command}"'), target_fixture="cli_run")
def when_run_lading_publish_command(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
    command: str,
) -> CliRunResult:
    """Execute the quoted publish command through the CLI test harness.

    Parameters
    ----------
    workspace_directory : Path
        Root of the staged workspace under test.
    repo_root : Path
        Repository root from which the CLI module is launched.
    preflight_test_context : PreflightTestContext
        Context bundling the cmd-mox double, overrides, and recorder.
    command : str
        Quoted CLI command; must begin with ``lading publish``.

    Returns
    -------
    CliRunResult
        The captured CLI run result.

    Raises
    ------
    AssertionError
        If the command is not a ``lading publish`` invocation.
    ValueError
        If ``command`` contains unterminated quoting that ``shlex.split``
        cannot parse.
    """  # ruff: ignore[docstring-extraneous-exception]  # ValueError comes from shlex.split, not a raise
    tokens = tuple(shlex.split(command))
    if tokens[:2] != ("lading", "publish"):
        message = f"Unexpected publish command: {command}"
        raise AssertionError(message)
    extra_args = tokens[2:]
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        *extra_args,
    )


@when(
    "I invoke lading publish with that workspace using --forbid-dirty",
    target_fixture="cli_run",
)
def when_invoke_lading_publish_forbid_dirty(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
) -> CliRunResult:
    """Execute the publish CLI with ``--forbid-dirty`` enabled.

    Parameters
    ----------
    workspace_directory : Path
        Root of the staged workspace under test.
    repo_root : Path
        Repository root from which the CLI module is launched.
    preflight_test_context : PreflightTestContext
        Context bundling the cmd-mox double, overrides, and recorder.

    Returns
    -------
    CliRunResult
        The captured CLI run result.
    """
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--forbid-dirty",
    )


@when(
    "I invoke lading publish with that workspace using --live",
    target_fixture="cli_run",
)
def when_invoke_lading_publish_live(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
) -> CliRunResult:
    """Execute the publish CLI with live publishing enabled.

    Parameters
    ----------
    workspace_directory : Path
        Root of the staged workspace under test.
    repo_root : Path
        Repository root from which the CLI module is launched.
    preflight_test_context : PreflightTestContext
        Context bundling the cmd-mox double, overrides, and recorder.

    Returns
    -------
    CliRunResult
        The captured CLI run result.
    """
    if not any(
        _is_cargo_publish_command(command)
        for command in preflight_test_context.overrides
    ):
        preflight_test_context.overrides["cargo", "publish"] = _CommandResponse(
            exit_code=0
        )
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--live",
    )


@when(
    parsers.parse(
        'I run lading publish with compiler-cache statistics written to "{name}"'
    ),
    target_fixture="cli_run",
)
def when_run_lading_publish_with_sccache_report(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
    name: str,
) -> CliRunResult:
    """Execute the publish CLI with ``--sccache-stats-json`` under the workspace.

    The report path is resolved against the scenario's workspace directory
    because the CLI runs from the repository root and a relative path would
    otherwise land there.

    Parameters
    ----------
    workspace_directory : Path
        Root of the staged workspace under test.
    repo_root : Path
        Repository root from which the CLI module is launched.
    preflight_test_context : PreflightTestContext
        Context bundling the cmd-mox double, overrides, and recorder.
    name : str
        Report file name, resolved under ``workspace_directory``.

    Returns
    -------
    CliRunResult
        The captured CLI run result.
    """
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--sccache-stats-json",
        str(workspace_directory / name),
    )
