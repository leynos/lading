"""Infrastructure helpers for publish BDD steps."""

import collections.abc as cabc
import contextlib
import dataclasses as dc
import os
import typing as typ

import pytest
from cmd_mox import Invocation

from lading.testing.cmd_mox_runner import normalize_cmd_mox_command

if typ.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox import CmdMox

    from .cli_run_types import CliRunResult


@dc.dataclass(frozen=True, slots=True)
class _CommandResponse:
    """Describe the outcome of a mocked command invocation.

    ``exit_code`` is the process status cmd-mox should report, while ``stdout``
    and ``stderr`` carry the captured streams a probe is expected to read back.
    """

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
        """Store one invocation under *label*."""
        self.records.append((label, args, env))

    def by_label(self, label: str) -> list[tuple[tuple[str, ...], dict[str, str]]]:
        """Return the arguments and environments recorded under *label*."""
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

    cmd_mox: CmdMox
    overrides: dict[tuple[str, ...], ResponseProvider]
    recorder: _PreflightInvocationRecorder

    def create_stub_config(self, *, allow_dirty: bool = True) -> _PreflightStubConfig:
        """Create stub configuration from this context.

        Parameters
        ----------
        allow_dirty : bool
            When ``True``, register cargo package/publish doubles that expect
            the ``--allow-dirty`` flag; otherwise omit it.

        Returns
        -------
        _PreflightStubConfig
            The stub configuration bound to this context.
        """
        return _create_stub_config(
            self.cmd_mox, self.overrides, self.recorder, allow_dirty=allow_dirty
        )


# ``Invocation`` is cmd-mox's own record, and ``runs`` is declared over it.
# A local structural protocol cannot stand in for it: ``Invocation.args`` is a
# ``list[str]`` and a protocol member is invariant, so the handler would not be
# assignable to the ``Callable[[Invocation], ...]`` the double accepts.
ResponseProvider = _CommandResponse | cabc.Callable[[Invocation], _CommandResponse]


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
        normalized_program, invocation_args = normalize_cmd_mox_command(
            program,
            argument_tuple,
        )
        return normalized_program, tuple(invocation_args)
    return program, argument_tuple


def _is_cargo_publish_command(command: tuple[str, ...]) -> bool:
    """Check whether the command tuple represents a cargo publish invocation."""
    return len(command) >= 2 and command[0] == "cargo" and command[1] == "publish"


def _make_preflight_handler(
    response: ResponseProvider,
    expected_arguments: tuple[str, ...],
    recorder: _PreflightInvocationRecorder | None,
    label: str,
) -> cabc.Callable[[Invocation], tuple[str, str, int]]:
    """Build a cmd-mox handler that validates argument prefixes."""

    def _handler(invocation: Invocation) -> tuple[str, str, int]:
        _validate_stub_arguments(expected_arguments, tuple(invocation.args))
        if isinstance(response, _CommandResponse):
            active_response = response
        else:
            active_response = response(invocation)
        if recorder is not None:
            env_mapping = dict(invocation.env)
            recorder.record(label, tuple(invocation.args), env_mapping)
        return (
            active_response.stdout,
            active_response.stderr,
            active_response.exit_code,
        )

    return _handler


def _matches_expected_prefix(
    expected: tuple[str, ...],
    received: tuple[str, ...],
) -> bool:
    """Return whether ``received`` begins with the expected argument tuple."""
    if len(received) < len(expected):
        return False
    return all(
        expected_arg == received[index] for index, expected_arg in enumerate(expected)
    )


def _make_preflight_dispatch_handler(
    entries: cabc.Sequence[tuple[tuple[str, ...], ResponseProvider]],
    recorder: _PreflightInvocationRecorder | None,
    label: str,
) -> cabc.Callable[[Invocation], tuple[str, str, int]]:
    """Build a handler that dispatches several prefixes for one command."""

    def _handler(invocation: Invocation) -> tuple[str, str, int]:
        received = tuple(invocation.args)
        for expected_arguments, response in entries:
            if _matches_expected_prefix(expected_arguments, received):
                if isinstance(response, _CommandResponse):
                    active_response = response
                else:
                    active_response = response(invocation)
                if recorder is not None:
                    env_mapping = dict(invocation.env)
                    recorder.record(label, received, env_mapping)
                return (
                    active_response.stdout,
                    active_response.stderr,
                    active_response.exit_code,
                )
        expected = ", ".join(str(arguments) for arguments, _response in entries)
        message = (
            f"Unexpected {label} invocation arguments: {received!r}; "
            f"expected one of {expected}"
        )
        raise AssertionError(message)

    return _handler


def _existing_static_stub_response(
    cmd_mox: CmdMox,
    program: str,
) -> tuple[tuple[str, ...], ResponseProvider] | None:
    """Return an existing static stub response for ``program`` if present."""
    double = getattr(cmd_mox, "_doubles", {}).get(program)
    if double is None or getattr(double, "kind", None) != "stub":
        return None
    response = getattr(double, "response", None)
    if response is None or getattr(double, "handler", None) is not None:
        return None
    expected_arguments = tuple(getattr(double.expectation, "args", ()))
    return (
        expected_arguments,
        _CommandResponse(
            exit_code=response.exit_code,
            stdout=response.stdout,
            stderr=response.stderr,
        ),
    )


def _is_passthrough_spy(double: object | None) -> bool:
    """Check whether an existing cmd-mox double is a passthrough spy."""
    return getattr(double, "kind", None) == "spy" and bool(
        getattr(double, "passthrough_mode", False)
    )


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


def _normalize_preflight_responses(
    config: _PreflightStubConfig,
) -> dict[tuple[str, ...], ResponseProvider]:
    """First publish override wins; package/publish --allow-dirty follows config."""
    defaults: dict[tuple[str, ...], ResponseProvider] = {
        ("git", "status", "--porcelain"): _CommandResponse(exit_code=0),
        ("git", "ls-files", "**/Cargo.lock", "Cargo.lock"): _CommandResponse(
            exit_code=0
        ),
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
            # Only the first cargo publish override wins; later ones are ignored.
            if publish_command_found:
                continue
            # Replace any caller-supplied --allow-dirty to match config.allow_dirty.
            base_args = tuple(arg for arg in command[2:] if arg != "--allow-dirty")
            publish_args = ("--allow-dirty",) if config.allow_dirty else ()
            publish_command = ("cargo", "publish", *publish_args, *base_args)
            publish_response = response
            publish_command_found = True
        else:
            if command[:2] == ("cargo", "package"):
                # Replace any caller-supplied --allow-dirty to match config.allow_dirty.
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
    return defaults


def _register_preflight_commands(
    config: _PreflightStubConfig,
) -> None:
    """Install cmd-mox doubles for publish pre-flight commands."""
    defaults = _normalize_preflight_responses(config)
    git_responses = {
        command[1:]: response
        for command, response in defaults.items()
        if command[0] == "git"
    }
    defaults = {
        command: response
        for command, response in defaults.items()
        if command[0] != "git"
    }
    _register_git_preflight_commands(config, git_responses)
    grouped_responses: dict[str, list[tuple[tuple[str, ...], ResponseProvider]]] = {}
    for command, response in defaults.items():
        expectation_program, expectation_args = _resolve_preflight_expectation(command)
        grouped_responses.setdefault(expectation_program, []).append((
            expectation_args,
            response,
        ))
    for expectation_program, entries in grouped_responses.items():
        existing_double = getattr(config.cmd_mox, "_doubles", {}).get(
            expectation_program
        )
        # Passthrough spies execute the real command and must stay registered.
        if _is_passthrough_spy(existing_double):
            continue
        existing = _existing_static_stub_response(config.cmd_mox, expectation_program)
        if existing is not None:
            entries.insert(0, existing)
        config.cmd_mox.stub(expectation_program).runs(
            _make_preflight_dispatch_handler(
                entries,
                config.recorder,
                expectation_program,
            )
        )


def _register_git_preflight_commands(
    config: _PreflightStubConfig,
    responses: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Register Git responses unless a passthrough spy is already active."""
    if not responses:
        return
    existing_double = getattr(config.cmd_mox, "_doubles", {}).get("git")
    # Passthrough spies execute the real command and must stay registered.
    if _is_passthrough_spy(existing_double):
        return
    config.cmd_mox.stub("git").runs(_make_git_handler(responses, config.recorder))


def _make_git_handler(
    responses: dict[tuple[str, ...], ResponseProvider],
    recorder: _PreflightInvocationRecorder | None,
) -> cabc.Callable[[Invocation], tuple[str, str, int]]:
    """Build a git handler that can serve multiple git subcommands."""

    def _handler(invocation: Invocation) -> tuple[str, str, int]:
        args = tuple(invocation.args)
        try:
            response = responses[args]
        except KeyError as exc:
            message = f"Unexpected git invocation arguments: {args!r}"
            raise AssertionError(message) from exc
        if isinstance(response, _CommandResponse):
            active_response = response
        else:
            active_response = response(invocation)
        if recorder is not None:
            env_mapping = dict(invocation.env)
            recorder.record("git", args, env_mapping)
        return (
            active_response.stdout,
            active_response.stderr,
            active_response.exit_code,
        )

    return _handler


def _build_env_restore_dict(var_name: str) -> dict[str, str]:
    """Build a dictionary for restoring an environment variable."""
    previous = os.environ.get(var_name)
    if previous is None:
        return {}
    return {var_name: previous}


@contextlib.contextmanager
def _cmd_mox_stub_env_enabled() -> cabc.Generator[None]:
    """Temporarily enable the lading cmd-mox stub environment flag."""
    var_name = "LADING_USE_CMD_MOX_STUB"
    restore = _build_env_restore_dict(var_name)
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
) -> CliRunResult:
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
def test_resolve_preflight_expectation_normalizes_cargo_commands(
    command: tuple[str, ...],
    expected_program: str,
    expected_args_prefix: tuple[str, ...],
) -> None:
    """Ensure cmd-mox expectations follow publish command normalization."""
    program, args_prefix = _resolve_preflight_expectation(command)

    assert program == expected_program, (
        "each cargo subcommand must resolve to its cmd-mox stub program"
    )
    assert args_prefix == expected_args_prefix, (
        "the resolved expectation must keep the arguments that followed the "
        "cargo subcommand"
    )
