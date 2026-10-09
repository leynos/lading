"""Then-step definitions for the publish pre-flight phase.

Pre-flight runs ``cargo test`` across the workspace, plus any auxiliary
builds, before a single crate is packaged. These steps assert what the
recorded invocations carried: which crates were excluded, which targets were
selected, and the environment the tests ran under.
"""

import collections.abc as cabc

from pytest_bdd import parsers, then

from .test_publish_helpers import (
    _get_required_invocations,
    _has_contiguous_args,
)
from .test_publish_infrastructure import _PreflightInvocationRecorder

_PREFLIGHT_TEST_MISSING = "cargo test pre-flight command was not invoked"


def _preflight_test_args(
    preflight_recorder: _PreflightInvocationRecorder,
) -> list[tuple[str, ...]]:
    """Return the argument tuple of every recorded cargo test pre-flight."""
    invocations = _get_required_invocations(
        preflight_recorder, "cargo::test", _PREFLIGHT_TEST_MISSING
    )
    return [args for args, _ in invocations]


def _excluded_from_preflight(
    preflight_recorder: _PreflightInvocationRecorder,
) -> set[str]:
    """Return every crate name cargo test pre-flight was told to exclude."""
    excluded: set[str] = set()
    for args in _preflight_test_args(preflight_recorder):
        excluded.update(
            args[index + 1]
            for index in range(len(args) - 1)
            if args[index] == "--exclude"
        )
    return excluded


@then(
    parsers.parse(
        'the publish command excludes crate "{crate_name}" from pre-flight tests'
    )
)
def then_publish_excludes_preflight_crate(
    preflight_recorder: _PreflightInvocationRecorder,
    crate_name: str,
) -> None:
    """Assert that cargo test pre-flight invocations skip ``crate_name``."""
    excluded = _excluded_from_preflight(preflight_recorder)
    assert crate_name in excluded, (
        f"expected {crate_name!r} among the pre-flight exclusions, "
        f"got {sorted(excluded)}"
    )


@then("the publish command limits pre-flight tests to libraries and binaries")
def then_publish_limits_preflight_targets(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that cargo test pre-flight invocations pass --lib and --bins."""
    args_list = _preflight_test_args(preflight_recorder)
    if not any(_has_contiguous_args(args, "--lib", "--bins") for args in args_list):
        message = (
            "Expected --lib followed by --bins in cargo test pre-flight invocations"
        )
        raise AssertionError(message)


@then("the publish command does not add pre-flight excludes")
def then_publish_has_no_preflight_excludes(
    preflight_recorder: _PreflightInvocationRecorder,
) -> None:
    """Assert that cargo test pre-flight invocations omit --exclude."""
    for args in _preflight_test_args(preflight_recorder):
        if "--exclude" in args:
            message = "Did not expect --exclude arguments in cargo test pre-flight"
            raise AssertionError(message)


def _assert_preflight_env_matches(
    preflight_recorder: _PreflightInvocationRecorder,
    predicate: cabc.Callable[[dict[str, str]], bool],
    message: str,
) -> None:
    """Assert some recorded pre-flight environment satisfies *predicate*."""
    invocations = _get_required_invocations(
        preflight_recorder, "cargo::test", _PREFLIGHT_TEST_MISSING
    )
    if not any(predicate(env) for _, env in invocations):
        raise AssertionError(message)


@then(parsers.parse('the cargo test pre-flight env contains "{name}"="{value}"'))
def then_cargo_test_env_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    name: str,
    value: str,
) -> None:
    """Assert that cargo test env propagates ``name`` with ``value``."""
    _assert_preflight_env_matches(
        preflight_recorder,
        lambda env: env.get(name) == value,
        f"Expected cargo test env {name}={value!r}",
    )


@then(parsers.parse('the cargo test pre-flight env includes "{snippet}" in RUSTFLAGS'))
def then_cargo_test_env_rustflags_contains(
    preflight_recorder: _PreflightInvocationRecorder,
    snippet: str,
) -> None:
    """Assert that cargo test RUSTFLAGS contains ``snippet``."""
    _assert_preflight_env_matches(
        preflight_recorder,
        lambda env: snippet in env.get("RUSTFLAGS", ""),
        f"Expected {snippet!r} in cargo test RUSTFLAGS",
    )


@then(parsers.parse('the publish command runs auxiliary build "{label}"'))
def then_publish_runs_aux_build(
    preflight_recorder: _PreflightInvocationRecorder,
    label: str,
) -> None:
    """Assert that an auxiliary build command was executed."""
    if not preflight_recorder.by_label(label):
        message = f"Expected auxiliary build invocation for {label}"
        raise AssertionError(message)
