"""When steps for publish BDD scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import when

from lading.commands import publish

from .test_publish_infrastructure import (
    ResponseProvider,
    _CommandResponse,
    _create_stub_config,
    _invoke_publish_with_options,
    _PreflightInvocationRecorder,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

_ImportedCmdMox = typ.Any  # type: ignore[assignment]


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


@when("I invoke lading publish with that workspace", target_fixture="cli_run")
def when_invoke_lading_publish(
    workspace_directory: Path,
    repo_root: Path,
    cmd_mox: _ImportedCmdMox,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
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
    cmd_mox: _ImportedCmdMox,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
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


@when(
    "I invoke lading publish with that workspace using --live",
    target_fixture="cli_run",
)
def when_invoke_lading_publish_live(
    workspace_directory: Path,
    repo_root: Path,
    cmd_mox: _ImportedCmdMox,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    preflight_recorder: _PreflightInvocationRecorder,
) -> dict[str, typ.Any]:
    """Execute the publish CLI with live publishing enabled."""
    if not any(command[:2] == ("cargo", "publish") for command in preflight_overrides):
        preflight_overrides[("cargo", "publish")] = _CommandResponse(exit_code=0)
    stub_config = _create_stub_config(cmd_mox, preflight_overrides, preflight_recorder)
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--live",
    )
