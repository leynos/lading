"""Given steps for publish BDD scenarios."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers

from lading.workspace import metadata as metadata_module

from .test_publish_infrastructure import (
    CmdMox,
    ResponseProvider,
    _CmdInvocation,
    _CommandResponse,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import pytest

try:
    from cmd_mox import CmdMox as _ImportedCmdMox
except ModuleNotFoundError:  # pragma: no cover - runtime fallback
    _ImportedCmdMox = CmdMox  # type: ignore[misc]


@given("cmd-mox IPC socket is unset")
def given_cmd_mox_socket_unset(
    monkeypatch: pytest.MonkeyPatch, cmd_mox: _ImportedCmdMox
) -> None:
    """Ensure cmd-mox stub usage fails due to a missing socket variable."""
    from cmd_mox import environment as env_mod

    del cmd_mox
    monkeypatch.delenv(env_mod.CMOX_IPC_SOCKET_ENV, raising=False)
    monkeypatch.setenv(metadata_module.CMD_MOX_STUB_ENV_VAR, "1")


@given("cargo check fails during publish pre-flight")
def given_cargo_check_fails(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a failing cargo check command."""
    preflight_overrides[("cargo", "check", "--workspace", "--all-targets")] = (
        _CommandResponse(exit_code=1, stderr="cargo check failed")
    )


@given("cargo test fails during publish pre-flight")
def given_cargo_test_fails(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a failing cargo test command."""
    preflight_overrides[("cargo", "test", "--workspace")] = _CommandResponse(
        exit_code=1, stderr="cargo test failed"
    )


@given(parsers.parse('cargo test fails with compiletest artifact "{relative_path}"'))
def given_cargo_test_fails_with_artifact(
    workspace_directory: Path,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    relative_path: str,
) -> None:
    """Create ``relative_path`` and configure cargo test to reference it."""
    artifact = workspace_directory / relative_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("line1\nline2\n", encoding="utf-8")
    preflight_overrides[("cargo", "test", "--workspace")] = _CommandResponse(
        exit_code=1,
        stderr=f"diff at {artifact}",
    )


@given(parsers.parse('cargo publish reports crate "{crate_name}" already uploaded'))
def given_cargo_publish_already_uploaded(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    crate_name: str,
) -> None:
    """Simulate cargo publish returning an already-uploaded error for ``crate_name``."""

    def _handler(invocation: _CmdInvocation) -> _CommandResponse:
        env_mapping = dict(getattr(invocation, "env", {}))
        if "PWD" not in env_mapping:
            message = (
                "cargo publish pre-flight stub expected PWD in the invocation "
                "environment"
            )
            raise AssertionError(message)
        cwd = Path(env_mapping["PWD"])
        if cwd.name == crate_name:
            error_message = (
                f"error: crate version `{crate_name} v0.1.0` is already uploaded"
            )
            return _CommandResponse(
                exit_code=101,
                stderr=error_message,
            )
        return _CommandResponse(exit_code=0)

    preflight_overrides[("cargo", "publish", "--dry-run")] = _handler


@given("the workspace has uncommitted changes")
def given_workspace_dirty(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a dirty working tree for git status."""
    preflight_overrides[("git", "status", "--porcelain")] = _CommandResponse(
        exit_code=0,
        stdout=" M Cargo.toml\n",
    )


@given(
    parsers.re(
        r'the preflight command "(?P<command>.+)" exits with '
        r'code (?P<exit_code>\d+) and stderr "(?P<stderr>.*)"'
    )
)
def given_preflight_command_override(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    command: str,
    exit_code: str,
    stderr: str,
) -> None:
    """Override an arbitrary pre-flight command with a custom result."""
    if tokens := tuple(segment for segment in command.split() if segment):
        preflight_overrides[tokens] = _CommandResponse(
            exit_code=int(exit_code),
            stderr=stderr,
        )
    else:
        message = "preflight command override requires tokens"
        raise AssertionError(message)
