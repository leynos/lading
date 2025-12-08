"""Infrastructure helpers for publish BDD steps."""

from __future__ import annotations

import contextlib
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
    allow_dirty: bool = True


@dc.dataclass(frozen=True, slots=True)
class PreflightTestContext:
    """Context for executing preflight tests with stubbed commands."""

    cmd_mox: typ.Any
    overrides: dict[tuple[str, ...], ResponseProvider]
    recorder: _PreflightInvocationRecorder

    def create_stub_config(self, *, allow_dirty: bool = True) -> _PreflightStubConfig:
        """Create stub configuration from this context."""
        return _create_stub_config(
            self.cmd_mox, self.overrides, self.recorder, allow_dirty=allow_dirty
        )


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
    *,
    allow_dirty: bool,
) -> _PreflightStubConfig:
    """Build a stub configuration that records preflight invocations."""
    return _PreflightStubConfig(
        cmd_mox,
        preflight_overrides,
        recorder=preflight_recorder,
        allow_dirty=allow_dirty,
    )


def _register_preflight_commands(
    config: _PreflightStubConfig,
) -> None:
    """Install cmd-mox doubles for publish pre-flight commands.

    Notes
    -----
    Only a single cargo publish override is honoured. If multiple cargo publish
    entries are present in ``config.overrides``, the first inserted entry
    determines the stubbed behaviour.

    """
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
        (
            "cargo",
            "package",
            *(("--allow-dirty",) if config.allow_dirty else ()),
        ): _CommandResponse(exit_code=0),
    }

    publish_command: tuple[str, ...]
    publish_response: ResponseProvider
    normalized_overrides: dict[tuple[str, ...], ResponseProvider] = {}
    publish_command_found = False
    for command, response in config.overrides.items():
        if _is_cargo_publish_command(command):
            base_args = tuple(arg for arg in command[2:] if arg != "--allow-dirty")
            publish_args = ("--allow-dirty",) if config.allow_dirty else ()
            publish_command = ("cargo", "publish", *publish_args, *base_args)
            publish_response = response
            publish_command_found = True
        else:
            if command[:2] == ("cargo", "package"):
                base_args = tuple(arg for arg in command[2:] if arg != "--allow-dirty")
                package_args = ("--allow-dirty",) if config.allow_dirty else ()
                command = ("cargo", "package", *package_args, *base_args)
            normalized_overrides[command] = response

    if not publish_command_found:
        publish_command = (
            "cargo",
            "publish",
            *(("--allow-dirty",) if config.allow_dirty else ()),
            "--dry-run",
        )
        publish_response = _CommandResponse(exit_code=0)

    defaults |= normalized_overrides
    defaults[publish_command] = publish_response
    for command, response in defaults.items():
        expectation_program, expectation_args = _resolve_preflight_expectation(command)
        config.cmd_mox.stub(expectation_program).runs(
            _make_preflight_handler(
                response, expectation_args, config.recorder, expectation_program
            )
        )


@contextlib.contextmanager
def _cmd_mox_stub_env_enabled() -> typ.Iterator[None]:
    """Temporarily enable CMD_MOX_STUB_ENV_VAR for cmd-mox stubs."""
    var_name = metadata_module.CMD_MOX_STUB_ENV_VAR
    previous = os.environ.get(var_name)
    restore = {var_name: previous} if previous is not None else {}
    os.environ[var_name] = "1"
    try:
        yield
    finally:
        os.environ.pop(var_name, None)
        os.environ.update(restore)


def _invoke_publish_with_options(
    repo_root: Path,
    workspace_directory: Path,
    stub_config: _PreflightStubConfig,
    *extra_args: str,
) -> dict[str, typ.Any]:
    """Register preflight doubles, enable stubs, and run the CLI."""
    from .test_common_steps import _run_cli

    _register_preflight_commands(stub_config)
    with _cmd_mox_stub_env_enabled():
        return _run_cli(repo_root, workspace_directory, "publish", *extra_args)


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
        (
            ("cargo", "publish", "--allow-dirty", "--dry-run"),
            "cargo::publish",
            ("--allow-dirty", "--dry-run"),
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
