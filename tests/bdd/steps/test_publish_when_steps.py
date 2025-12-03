"""When steps for publish BDD scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import when

from lading.commands import publish

from .test_publish_infrastructure import (
    PreflightTestContext,
    _CommandResponse,
    _invoke_publish_with_options,
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
    preflight_test_context: PreflightTestContext,
) -> dict[str, typ.Any]:
    """Execute the publish CLI via ``python -m`` and capture the result."""
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(repo_root, workspace_directory, stub_config)


@when(
    "I invoke lading publish with that workspace using --forbid-dirty",
    target_fixture="cli_run",
)
def when_invoke_lading_publish_forbid_dirty(
    workspace_directory: Path,
    repo_root: Path,
    preflight_test_context: PreflightTestContext,
) -> dict[str, typ.Any]:
    """Execute the publish CLI with ``--forbid-dirty`` enabled."""
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
) -> dict[str, typ.Any]:
    """Execute the publish CLI with live publishing enabled."""
    if not any(
        command[:2] == ("cargo", "publish")
        for command in preflight_test_context.overrides
    ):
        preflight_test_context.overrides[("cargo", "publish")] = _CommandResponse(
            exit_code=0
        )
    stub_config = preflight_test_context.create_stub_config()
    return _invoke_publish_with_options(
        repo_root,
        workspace_directory,
        stub_config,
        "--live",
    )
