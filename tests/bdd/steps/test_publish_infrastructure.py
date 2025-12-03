"""Infrastructure helpers for publish BDD steps."""

from __future__ import annotations

import dataclasses as dc
import os
import typing as typ

import pytest

from lading.commands import publish
from lading.workspace import metadata as metadata_module

try:
    from cmd_mox import CmdMox
except ModuleNotFoundError:  # pragma: no cover - runtime fallback
    CmdMox = typ.Any  # type: ignore[assignment]

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tomlkit.toml_document import TOMLDocument  # pragma: no cover

    from .test_common_steps import _run_cli  # noqa: F401
else:  # pragma: no cover - runtime fallback for typing helpers
    Path = typ.Any  # type: ignore[assignment]
    TOMLDocument = typ.Any  # type: ignore[assignment]


@dc.dataclass(frozen=True, slots=True)
class _CommandResponse:
    """Describe the outcome of a mocked command invocation."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dc.dataclass(slots=True)
class _PreflightInvocationRecorder:
    """Collect arguments recorded from cmd-mox double invocations."""

    records: list[tuple[str, tuple[str, ...], dict[str, str]]] = dc.field(
        default_factory=list
    )

    def record(self, label: str, args: tuple[str, ...], env: dict[str, str]) -> None:
        self.records.append((label, args, env))

    def by_label(self, label: str) -> list[tuple[tuple[str, ...], dict[str, str]]]:
        return [
            (args, env)
            for entry_label, args, env in self.records
            if entry_label == label
        ]


@dc.dataclass(frozen=True, slots=True)
class _PreflightStubConfig:
    """Configuration for cmd-mox preflight command stubs."""

    cmd_mox: CmdMox
    overrides: dict[tuple[str, ...], ResponseProvider] = dc.field(default_factory=dict)
    recorder: _PreflightInvocationRecorder | None = None


@dc.dataclass
class PreflightTestContext:
    """Context for executing preflight tests with stubbed commands."""

    cmd_mox: typ.Any
    overrides: dict[tuple[str, ...], ResponseProvider]
    recorder: _PreflightInvocationRecorder

    def create_stub_config(self) -> _PreflightStubConfig:
        """Create stub configuration from this context."""
        return _create_stub_config(self.cmd_mox, self.overrides, self.recorder)


class _CmdInvocation(typ.Protocol):
    """Protocol describing the cmd-mox invocation payload."""

    args: typ.Sequence[str]


ResponseProvider = _CommandResponse | typ.Callable[[_CmdInvocation], _CommandResponse]


def _validate_stub_arguments(
    expected: tuple[str, ...],
    received: tuple[str, ...],
) -> None:
    """Validate that received arguments match the expected prefix."""
    if not expected:
        return

    if len(received) < len(expected):
        message = "Received fewer arguments than expected for preflight stub"
        raise AssertionError(message)

    for index, expected_arg in enumerate(expected):
        if expected_arg != received[index]:
            message = (
                "Preflight stub mismatch: expected argument prefix "
                f"{expected_arg!r} at position {index}, got "
                f"{received[index]!r}"
            )
            raise AssertionError(message)


def _resolve_preflight_expectation(
    command: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Return the cmd-mox program and argument prefix for ``command``."""
    program, *args = command
    argument_tuple = tuple(args)
    if program == "cargo":
        normalised_program, invocation_args = publish._normalise_cmd_mox_command(
            program,
            argument_tuple,
        )
        return normalised_program, tuple(invocation_args)
    return program, argument_tuple


def _is_cargo_publish_command(command: tuple[str, ...]) -> bool:
    """Check whether the command tuple represents a cargo publish invocation."""
    return len(command) >= 2 and command[0] == "cargo" and command[1] == "publish"


def _make_preflight_handler(
    response: ResponseProvider,
    expected_arguments: tuple[str, ...],
    recorder: _PreflightInvocationRecorder | None,
    label: str,
) -> typ.Callable[[_CmdInvocation], tuple[str, str, int]]:
    """Build a cmd-mox handler that validates argument prefixes."""

    def _handler(invocation: _CmdInvocation) -> tuple[str, str, int]:
        _validate_stub_arguments(expected_arguments, tuple(invocation.args))
        active_response = response(invocation) if callable(response) else response
        if recorder is not None:
            env_mapping = dict(getattr(invocation, "env", {}))
            recorder.record(label, tuple(invocation.args), env_mapping)
        return (
            active_response.stdout,
            active_response.stderr,
            active_response.exit_code,
        )

    return _handler


def _create_stub_config(
    cmd_mox: CmdMox,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    preflight_recorder: _PreflightInvocationRecorder,
) -> _PreflightStubConfig:
    """Build a stub configuration that records preflight invocations."""
    return _PreflightStubConfig(
        cmd_mox,
        preflight_overrides,
        recorder=preflight_recorder,
    )


def _register_preflight_commands(config: _PreflightStubConfig) -> None:
    """Install cmd-mox doubles for publish pre-flight commands."""
    defaults: dict[tuple[str, ...], ResponseProvider] = {
        ("git", "status", "--porcelain"): _CommandResponse(exit_code=0),
        (
            "cargo",
            "check",
            "--workspace",
            "--all-targets",
        ): _CommandResponse(exit_code=0),
        (
            "cargo",
            "test",
            "--workspace",
        ): _CommandResponse(exit_code=0),
        ("cargo", "package"): _CommandResponse(exit_code=0),
    }

    publish_command: tuple[str, ...]
    publish_response: ResponseProvider
    for command, response in config.overrides.items():
        if _is_cargo_publish_command(command):
            publish_command = command
            publish_response = response
            break
    else:
        publish_command = ("cargo", "publish", "--dry-run")
        publish_response = _CommandResponse(exit_code=0)

    filtered_overrides = {
        command: response
        for command, response in config.overrides.items()
        if not _is_cargo_publish_command(command)
    }

    defaults.update(filtered_overrides)
    defaults[publish_command] = publish_response
    for command, response in defaults.items():
        expectation_program, expectation_args = _resolve_preflight_expectation(command)
        config.cmd_mox.stub(expectation_program).runs(
            _make_preflight_handler(
                response, expectation_args, config.recorder, expectation_program
            )
        )


def _invoke_publish_with_options(
    repo_root: Path,
    workspace_directory: Path,
    stub_config: _PreflightStubConfig,
    *extra_args: str,
) -> dict[str, typ.Any]:
    """Register preflight doubles, enable stubs, and run the CLI."""
    from .test_common_steps import _run_cli

    _register_preflight_commands(stub_config)
    previous = os.environ.get(metadata_module.CMD_MOX_STUB_ENV_VAR)
    os.environ[metadata_module.CMD_MOX_STUB_ENV_VAR] = "1"
    try:
        return _run_cli(repo_root, workspace_directory, "publish", *extra_args)
    finally:
        if previous is None:
            os.environ.pop(metadata_module.CMD_MOX_STUB_ENV_VAR, None)
        else:
            os.environ[metadata_module.CMD_MOX_STUB_ENV_VAR] = previous


@pytest.mark.parametrize(
    ("command", "expected_program", "expected_args_prefix"),
    [
        (("cargo", "check"), "cargo::check", ()),
        (("cargo", "test"), "cargo::test", ()),
        (("cargo", "clippy"), "cargo::clippy", ()),
        (("cargo", "fmt"), "cargo::fmt", ()),
        (("cargo", "build"), "cargo::build", ()),
        (("cargo", "doc"), "cargo::doc", ()),
        (
            ("cargo", "test", "--package", "foo", "--", "--ignored"),
            "cargo::test",
            ("--package", "foo", "--", "--ignored"),
        ),
    ],
)
def test_resolve_preflight_expectation_normalises_cargo_commands(
    command: tuple[str, ...],
    expected_program: str,
    expected_args_prefix: tuple[str, ...],
) -> None:
    """Ensure cmd-mox expectations follow publish command normalisation."""
    program, args_prefix = _resolve_preflight_expectation(command)

    assert program == expected_program
    assert args_prefix == expected_args_prefix
