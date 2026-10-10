"""Then-step definitions for the order of cargo package and publish calls.

A publish is only correct if the crates go out in dependency order, and if
live mode packages each crate before publishing it. These steps read the
recorded ``cargo::package`` and ``cargo::publish`` invocations and assert
that order, and that each mode passes the flag it is supposed to.
"""

from pytest_bdd import parsers, then

from .test_publish_helpers import (
    _assert_crate_order_matches,
    _assert_invocations_have_flag,
    _assert_invocations_lack_flag,
    _extract_crate_names_from_invocations,
    _get_required_invocations,
    _split_names,
)
from .test_publish_infrastructure import _PreflightInvocationRecorder


def _publish_invocations(
    preflight_recorder: _PreflightInvocationRecorder,
) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    """Return the recorded cargo publish invocations."""
    return _get_required_invocations(
        preflight_recorder,
        "cargo::publish",
        "cargo publish was not invoked for publishable crates",
    )


def _assert_publish_order(
    preflight_recorder: _PreflightInvocationRecorder,
    crate_names: str,
    context: str,
) -> None:
    """Assert the recorded publish invocations name crates in ``crate_names`` order."""
    _assert_crate_order_matches(
        _extract_crate_names_from_invocations(_publish_invocations(preflight_recorder)),
        _split_names(crate_names),
        context,
    )


@then(parsers.parse('the publish command packages crates in order "{crate_names}"'))
def then_publish_packages_crates_in_order(
    preflight_recorder: _PreflightInvocationRecorder,
    crate_names: str,
) -> None:
    """Assert that cargo package ran for each crate in publish order."""
    invocations = _get_required_invocations(
        preflight_recorder,
        "cargo::package",
        "cargo package was not invoked for publishable crates",
    )
    _assert_crate_order_matches(
        _extract_crate_names_from_invocations(invocations),
        _split_names(crate_names),
        "cargo package",
    )


@then(
    parsers.parse(
        'the publish command performs cargo publish dry-run for crates "{crate_names}"'
    )
)
def then_publish_runs_dry_run(
    preflight_recorder: _PreflightInvocationRecorder, crate_names: str
) -> None:
    """Assert that cargo publish --dry-run runs for each crate in order."""
    _assert_invocations_have_flag(
        _publish_invocations(preflight_recorder), "--dry-run", "cargo publish"
    )
    _assert_publish_order(preflight_recorder, crate_names, "cargo publish --dry-run")


@then(
    parsers.parse(
        'the publish command performs live cargo publish for crates "{crate_names}"'
    )
)
def then_publish_runs_live(
    preflight_recorder: _PreflightInvocationRecorder, crate_names: str
) -> None:
    """Assert that live cargo publish runs without the dry-run flag."""
    _assert_invocations_lack_flag(
        _publish_invocations(preflight_recorder), "--dry-run", "cargo publish"
    )
    _assert_publish_order(preflight_recorder, crate_names, "cargo publish live order")


@then(
    parsers.parse(
        "the publish command interleaves live package and publish for crates "
        '"{crate_names}"'
    )
)
def then_publish_interleaves_live_package_and_publish(
    preflight_recorder: _PreflightInvocationRecorder, crate_names: str
) -> None:
    """Assert live publish packages and publishes each crate before the next."""
    filtered = [
        (label, (args, env))
        for label, args, env in preflight_recorder.records
        if label in {"cargo::package", "cargo::publish"}
    ]
    labels = [label for label, _ in filtered]
    observed_sequence = list(
        zip(
            labels,
            _extract_crate_names_from_invocations([
                invocation for _, invocation in filtered
            ]),
            strict=True,
        )
    )

    expected_sequence: list[tuple[str, str]] = []
    for crate_name in _split_names(crate_names):
        expected_sequence.extend([
            ("cargo::package", crate_name),
            ("cargo::publish", crate_name),
        ])

    if observed_sequence != expected_sequence:
        message = (
            "Unexpected live package/publish order: "
            f"observed={observed_sequence!r}, expected={expected_sequence!r}"
        )
        raise AssertionError(message)
