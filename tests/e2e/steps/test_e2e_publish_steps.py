"""Step definitions for end-to-end lading publish scenarios.

These steps share the workspace fixtures in :mod:`tests.e2e.steps.test_e2e_steps`
but assert on the publish invocation record, so they live apart from the bump
and lockfile steps.
"""

import typing as typ
from pathlib import Path

from pytest_bdd import parsers, then, when

from tests.e2e.helpers.e2e_steps_helpers import (
    _CliRunResult,
    filter_records,
    run_lading_in_e2e_workspace,
)

if typ.TYPE_CHECKING:
    from tests.e2e.helpers import workspace_builder


@when(
    "I run lading publish --forbid-dirty in the E2E workspace", target_fixture="cli_run"
)
def when_run_lading_publish(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
) -> _CliRunResult:
    """Invoke `lading publish` (dry-run default) with `--forbid-dirty`.

    Parameters
    ----------
    repo_root : Path
        Repository root from which the CLI module is executed.
    e2e_state : dict[str, typ.Any]
        Shared step state carrying the target ``workspace``.

    Returns
    -------
    _CliRunResult
        The captured CLI result mapping with the executed ``command``,
        ``returncode``, ``stdout``, ``stderr``, and ``workspace_root``.

    Examples
    --------
    Bound to the Gherkin ``when`` step and invoked by pytest-bdd; the
    returned mapping is exposed as the ``cli_run`` fixture::

        >>> result = when_run_lading_publish(repo_root, e2e_state)  # doctest: +SKIP
        >>> result["returncode"]  # doctest: +SKIP
        0
    """
    return run_lading_in_e2e_workspace(
        repo_root, e2e_state, "publish", "--forbid-dirty"
    )


@when("I run lading publish in the E2E workspace", target_fixture="cli_run")
def when_run_lading_publish_allow_dirty(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
) -> _CliRunResult:
    """Invoke `lading publish` using the default allow-dirty behaviour.

    Parameters
    ----------
    repo_root : Path
        Repository root from which the CLI module is executed.
    e2e_state : dict[str, typ.Any]
        Shared step state carrying the target ``workspace``.

    Returns
    -------
    _CliRunResult
        The captured CLI result mapping with the executed ``command``,
        ``returncode``, ``stdout``, ``stderr``, and ``workspace_root``.

    Examples
    --------
    Bound to the Gherkin ``when`` step and invoked by pytest-bdd; the
    returned mapping is exposed as the ``cli_run`` fixture::

        >>> result = when_run_lading_publish_allow_dirty(  # doctest: +SKIP
        ...     repo_root, e2e_state
        ... )
        >>> result["returncode"]  # doctest: +SKIP
        0
    """
    return run_lading_in_e2e_workspace(repo_root, e2e_state, "publish")


def _crate_names_called(publish_spies: dict[str, typ.Any], record: str) -> list[str]:
    """Return the crate directory each ``record`` invocation ran in."""
    return [
        Path(env["PWD"]).name
        for _label, _args, env in filter_records(publish_spies, record)
    ]


def _assert_one_call_per_crate(publish_spies: dict[str, typ.Any], record: str) -> None:
    """Assert ``record`` ran exactly once in every crate directory."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    called = _crate_names_called(publish_spies, record)
    assert sorted(called) == sorted(workspace.crate_names), called


@then("cargo preflight was run for the workspace")
def then_cargo_preflight_ran(publish_spies: dict[str, typ.Any]) -> None:
    """Assert publish executed cargo check and cargo test pre-flight commands."""
    check_calls = filter_records(publish_spies, "cargo::check")
    test_calls = filter_records(publish_spies, "cargo::test")
    assert check_calls, "expected at least one cargo::check preflight invocation"
    assert test_calls, "expected at least one cargo::test preflight invocation"


@then(parsers.parse('the publish order is "{expected}"'))
def then_publish_order(publish_spies: dict[str, typ.Any], expected: str) -> None:
    """Assert cargo package calls occur in the expected crate order.

    Parameters
    ----------
    publish_spies : dict[str, typ.Any]
        Recorded publish invocation state produced by the stub handlers.
    expected : str
        Comma-separated crate names parsed from the scenario step.

    Examples
    --------
    Bound to the Gherkin ``then`` step and invoked by pytest-bdd against the
    recorded ``publish_spies`` state::

        >>> then_publish_order(publish_spies, "core, utils, app")  # doctest: +SKIP
    """
    expected_names = [name.strip() for name in expected.split(",") if name.strip()]
    seen = _crate_names_called(publish_spies, "cargo::package")
    assert seen == expected_names, f"expected {expected_names!r}, got {seen!r}"


@then("cargo package was invoked for each crate")
def then_cargo_package_invoked(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo package was invoked once per crate."""
    _assert_one_call_per_crate(publish_spies, "cargo::package")


@then("cargo publish --dry-run was invoked for each crate")
def then_cargo_publish_invoked(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish --dry-run was invoked once per crate."""
    _assert_one_call_per_crate(publish_spies, "cargo::publish")


@then("cargo publish uses --allow-dirty in the default publish flow")
def then_cargo_publish_uses_allow_dirty(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish includes --allow-dirty when allow-dirty is enabled."""
    publish_calls = filter_records(publish_spies, "cargo::publish")
    assert publish_calls, "expected at least one cargo::publish invocation"
    seen_args = {args for _label, args, _env in publish_calls}
    assert seen_args == {("--allow-dirty", "--dry-run")}, seen_args


@then("cargo publish omits --allow-dirty when forbid-dirty is set")
def then_cargo_publish_omits_allow_dirty(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish omits --allow-dirty when allow-dirty is disabled."""
    publish_calls = filter_records(publish_spies, "cargo::publish")
    assert publish_calls, "expected at least one cargo::publish invocation"
    seen_args = {args for _label, args, _env in publish_calls}
    assert seen_args == {("--dry-run",)}, seen_args
    assert all("--allow-dirty" not in args for args in seen_args), seen_args
