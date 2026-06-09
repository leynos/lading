"""Publish run ordering and interleaving test coverage."""

from __future__ import annotations

import collections.abc as cabc
import logging
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lading.commands import publish

from .conftest import (
    CARGO_PACKAGE,
    CARGO_PUBLISH,
    CARGO_PUBLISH_DRY_RUN,
    INDEX_MISSING_STDERR_BETA,
    CallTrackingRunner,
    make_config,
    make_dependency_chain,
    make_n_crate_chain,
    make_workspace,
)


def _make_beta_package_index_failure_runner() -> cabc.Callable[
    [cabc.Sequence[str]], tuple[int, str, str]
]:
    """Return a runner that simulates beta failing cargo package."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env
        is_package = tuple(command[:2]) == ("cargo", "package")
        is_beta = cwd is not None and cwd.name == "beta"
        if is_package and is_beta:
            return (1, "", INDEX_MISSING_STDERR_BETA)
        return (0, "", "")

    return runner


def test_run_logs_when_unpublished_workspace_dependency_override_is_enabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``run`` downgrades in-plan index misses when the override is enabled."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_dependency_chain(root))
    configuration = make_config()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=_make_beta_package_index_failure_runner(),
            allow_unpublished_workspace_deps=True,
        ),
    )

    assert (
        "Allowing unpublished workspace dependencies during dry-run publish"
        in caplog.messages
    ), "override-enabled banner should be logged"
    assert any(
        "cargo package for crate beta could not resolve sibling dependency alpha"
        in message
        and "unpublished workspace dependency override is enabled" in message
        for message in caplog.messages
    ), "downgraded index-miss warning should mention the enabled override"


def test_run_honours_explicit_unpublished_workspace_deps_opt_out(
    tmp_path: Path,
) -> None:
    """Explicit ``False`` keeps dry-run index-missing-version failures strict."""
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_dependency_chain(root))
    configuration = make_config()

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish.run(
            root,
            configuration,
            workspace,
            options=publish.PublishOptions(
                build_directory=tmp_path / "build",
                command_runner=_make_beta_package_index_failure_runner(),
                allow_unpublished_workspace_deps=False,
            ),
        )

    assert "unpublished workspace dependency override" in str(excinfo.value), (
        "strict failure should reference the opt-out override"
    )


@pytest.mark.parametrize(
    "crate_count",
    [1, 2, 3, 5],
    ids=["one_crate", "two_crates", "three_crates", "five_crates"],
)
def test_run_keeps_dry_run_publication_batched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, crate_count: int
) -> None:
    """Dry-run publish still packages all crates before publish dry-runs.

    Assert full ``(command, cwd)`` pairs to confirm each crate operation
    runs in the correct staging directory.
    """
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=runner,
            live=False,
        ),
    )

    staging_root = (tmp_path / "build") / root.name
    expected_packages = [
        (CARGO_PACKAGE, staging_root / crate.root_path.relative_to(root))
        for crate in workspace.crates
    ]
    expected_dry_runs = [
        (CARGO_PUBLISH_DRY_RUN, staging_root / crate.root_path.relative_to(root))
        for crate in workspace.crates
    ]
    assert runner.calls == expected_packages + expected_dry_runs, (
        "dry-run must package every crate before dry-running any publish"
    )
    assert f"Starting publish workflow for workspace {root}" in caplog.messages, (
        "workflow start should be logged"
    )
    assert any(
        message.startswith("Preparing staged workspace for publication under ")
        for message in caplog.messages
    ), "staging preparation should be logged"
    assert any(
        message.startswith("Staged workspace created at ")
        for message in caplog.messages
    ), "staged workspace creation should be logged"
    assert (
        "Workspace README staging skipped; handled by lading bump" in caplog.messages
    ), "README staging skip should be logged"
    assert (
        f"Publish workflow completed successfully for workspace {root}"
        in caplog.messages
    ), "successful completion should be logged"


@pytest.mark.parametrize(
    "crate_count",
    [1, 2, 3, 5],
    ids=["one_crate", "two_crates", "three_crates", "five_crates"],
)
def test_run_keeps_live_publication_interleaved(
    tmp_path: Path, crate_count: int
) -> None:
    """Live publish packages and publishes each crate before advancing."""
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=runner,
            live=True,
        ),
    )

    staging_root = (tmp_path / "build") / root.name
    expected_pairs = [
        (command, staging_root / crate.root_path.relative_to(root))
        for crate in workspace.crates
        for command in (CARGO_PACKAGE, CARGO_PUBLISH)
    ]
    assert runner.calls == expected_pairs, (
        "live publish must package then publish each crate before advancing"
    )


@given(st.integers(min_value=1, max_value=10))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_dry_run_batching_invariant_property(tmp_path: Path, crate_count: int) -> None:
    """Dry-run publication packages every crate before publishing any crate."""
    root = tmp_path / f"workspace_{crate_count}"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / f"build_{crate_count}",
            command_runner=runner,
            live=False,
        ),
    )

    expected_packages = [
        (CARGO_PACKAGE, call_cwd)
        for command, call_cwd in runner.calls
        if command == CARGO_PACKAGE
    ]
    expected_dry_runs = [
        (CARGO_PUBLISH_DRY_RUN, call_cwd)
        for command, call_cwd in runner.calls
        if command == CARGO_PUBLISH_DRY_RUN
    ]
    assert len(expected_packages) == crate_count, "every crate should be packaged once"
    assert len(expected_dry_runs) == crate_count, (
        "every crate should be dry-run published once"
    )
    assert runner.calls == expected_packages + expected_dry_runs, (
        "all packaging must precede all publish dry-runs"
    )


@given(st.integers(min_value=1, max_value=10))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_live_interleaving_invariant_property(tmp_path: Path, crate_count: int) -> None:
    """Live publication packages and publishes each crate before advancing."""
    root = tmp_path / f"workspace_{crate_count}"
    workspace = make_workspace(root, *make_n_crate_chain(root, crate_count))
    configuration = make_config()
    runner = CallTrackingRunner()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / f"build_{crate_count}",
            command_runner=runner,
            live=True,
        ),
    )

    assert len(runner.calls) == crate_count * 2, (
        "each crate should yield one package and one publish call"
    )
    for package_call, publish_call in zip(
        runner.calls[::2], runner.calls[1::2], strict=True
    ):
        package_command, package_cwd = package_call
        publish_command, publish_cwd = publish_call
        assert package_command == CARGO_PACKAGE, "package call should precede publish"
        assert publish_command == CARGO_PUBLISH, "publish call should follow package"
        assert package_cwd == publish_cwd, (
            "package and publish should run in the same crate directory"
        )
