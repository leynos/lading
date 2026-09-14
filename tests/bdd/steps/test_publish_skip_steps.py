"""Steps for the publish pre-flight skip scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers, then

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import pytest

    from .test_publish_infrastructure import _PreflightInvocationRecorder

_BUILD_CHECK_LABELS = ("cargo::check", "cargo::test")


@given(parsers.parse('the environment variable {variable} is set to "{value}"'))
def given_environment_variable_set(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """Set ``variable`` for the CLI subprocess this scenario launches.

    The CLI runs as a child process that inherits ``os.environ``, so setting
    the variable here is what exercises the environment-variable default.
    """
    monkeypatch.setenv(variable, value)


@then("the publish command runs no pre-flight build commands")
def then_no_preflight_build_commands(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that no auxiliary build, cargo check, or cargo test ran.

    The lockfile freshness probe still runs, and it invokes ``cargo
    metadata``, so the assertion names the build commands rather than cargo
    as a whole.

    Raises
    ------
    AssertionError
        If a pre-flight ``cargo check`` or ``cargo test`` invocation was
        recorded.
    """
    recorded = [
        label for label in _BUILD_CHECK_LABELS if preflight_recorder.by_label(label)
    ]
    if recorded:
        message = f"Expected no pre-flight build commands, recorded: {recorded}"
        raise AssertionError(message)


@then("the publish command runs the pre-flight cargo check and cargo test")
def then_preflight_cargo_commands_ran(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that both pre-flight cargo commands were invoked.

    Raises
    ------
    AssertionError
        If either pre-flight cargo command is missing.
    """
    missing = [
        label for label in _BUILD_CHECK_LABELS if not preflight_recorder.by_label(label)
    ]
    if missing:
        message = f"Expected pre-flight cargo commands, missing: {missing}"
        raise AssertionError(message)


def _require(*, condition: bool, message: str) -> None:
    """Raise :class:`AssertionError` with ``message`` unless ``condition`` holds."""
    if not condition:
        raise AssertionError(message)


@then("the publish command still verified the working tree and tracked lockfiles")
def then_cheap_guards_ran(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert the guards that a skip must never remove still ran.

    Discovery alone would still pass with the freshness probe removed, so the
    locked probe itself is asserted. Cargo metadata is recorded under the plain
    ``cargo`` label; only check and test are namespaced.
    """
    git_invocations = [args for args, _env in preflight_recorder.by_label("git")]
    cargo_invocations = [args for args, _env in preflight_recorder.by_label("cargo")]
    _require(
        condition=("status", "--porcelain") in git_invocations,
        message=f"Expected a working-tree check, recorded: {git_invocations}",
    )
    _require(
        condition=any(args[:1] == ("ls-files",) for args in git_invocations),
        message=f"Expected lockfile discovery, recorded: {git_invocations}",
    )
    _require(
        condition=any(
            args[:2] == ("metadata", "--locked") for args in cargo_invocations
        ),
        message=f"Expected a locked freshness probe, recorded: {cargo_invocations}",
    )


__all__ = [
    "given_environment_variable_set",
    "then_cheap_guards_ran",
    "then_no_preflight_build_commands",
    "then_preflight_cargo_commands_ran",
]
