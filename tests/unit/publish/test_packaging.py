"""Unit tests for publish crate packaging workflow."""

from __future__ import annotations

import logging
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

import pytest

from lading.commands import publish

from .conftest import (
    CallTrackingRunner,
    make_config,
    make_dependency_chain,
    make_failing_runner,
    make_workspace,
    prepare_staging_root,
)


def _assert_packaging_failure_message_contains(
    plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation],
    runner: cabc.Callable[..., tuple[int, str, str]],
    expected_in_message: str,
    not_expected_in_message: str | None = None,
) -> None:
    """Assert that packaging failure produces expected error message content."""
    plan, preparation = plan_and_prep

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._package_publishable_crates(
            plan,
            preparation,
            options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
            runner=runner,
        )

    message = str(excinfo.value)
    assert "cargo package failed for crate alpha" in message
    assert expected_in_message in message
    if not_expected_in_message is not None:
        assert not_expected_in_message not in message


def test_package_publishable_crates_runs_in_plan_order(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Cargo package is invoked for every publishable crate in plan order."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish._package_publishable_crates(
        plan,
        preparation,
        options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
        runner=runner,
    )

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [
        (("cargo", "package", "--allow-dirty"), root) for root in expected_roots
    ], "cargo package should run once per publishable crate in order"


def test_package_publishable_crates_stops_on_failure(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Failures during packaging abort the workflow with crate context."""
    plan, preparation, _staging_root = publish_plan_and_prep
    calls: list[str] = []
    failing_runner = make_failing_runner(stderr="packaging failed")

    def tracked_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        calls.append(" ".join(command))
        return failing_runner(command, cwd=cwd, env=env)

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._package_publishable_crates(
            plan,
            preparation,
            options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
            runner=tracked_runner,
        )

    assert calls == ["cargo package --allow-dirty"]
    assert "cargo package failed for crate alpha" in str(excinfo.value)
    assert "packaging failed" in str(excinfo.value)


def test_package_publishable_crates_reports_stdout_on_failure(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Failure details fall back to stdout when stderr is empty."""
    plan_and_prep = publish_plan_and_prep[:2]
    stdout_failure = make_failing_runner(stdout="stdout failure details")

    _assert_packaging_failure_message_contains(
        plan_and_prep,
        stdout_failure,
        expected_in_message="stdout failure details",
    )


def test_package_publishable_crates_prefers_stderr_over_stdout(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Error detail prefers stderr when both streams are populated."""
    plan_and_prep = publish_plan_and_prep[:2]
    both_populated = make_failing_runner(stdout="stdout detail", stderr="stderr detail")

    _assert_packaging_failure_message_contains(
        plan_and_prep,
        both_populated,
        expected_in_message="stderr detail",
        not_expected_in_message="stdout detail",
    )


def test_publish_crates_run_dry_run_in_order(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cargo publish --dry-run runs for each crate in publish order."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish._publish_crates(
        plan,
        preparation,
        runner=runner,
        options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
    )

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [
        (("cargo", "publish", "--allow-dirty", "--dry-run"), root)
        for root in expected_roots
    ]
    assert any("cargo publish" in message for message in caplog.messages)


def test_publish_crates_run_live_without_dry_run(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Live mode omits the --dry-run flag when publishing crates."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish._publish_crates(
        plan,
        preparation,
        runner=runner,
        options=publish._PublishExecutionOptions(live=True, allow_dirty=True),
    )

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [
        (("cargo", "publish", "--allow-dirty"), root) for root in expected_roots
    ]


@pytest.mark.parametrize(
    "live",
    [pytest.param(False, id="dry-run"), pytest.param(True, id="live")],
)
def test_publish_crates_continue_when_version_already_uploaded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, *, live: bool
) -> None:
    """Already-published versions log a warning and continue."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    workspace_root = tmp_path / "workspace"
    alpha, beta, _gamma = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, alpha, beta), make_config()
    )
    staging_root = prepare_staging_root(plan, tmp_path)
    preparation = publish.PublishPreparation(
        staging_root=staging_root,
        copied_readmes=(),
    )

    calls: list[str] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env
        crate_name = "" if cwd is None else cwd.name
        calls.append(crate_name)
        if crate_name == "alpha":
            return (
                101,
                "",
                "error: crate version `alpha v0.1.0` is already uploaded",
            )
        return (0, "", "")

    publish._publish_crates(
        plan,
        preparation,
        runner=runner,
        options=publish._PublishExecutionOptions(live=live, allow_dirty=True),
    )

    assert calls == ["alpha", "beta"]
    assert any("already published" in message for message in caplog.messages)


def test_publish_crates_raise_on_failure(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Unexpected cargo publish failures abort the workflow."""
    plan, preparation, _staging_root = publish_plan_and_prep
    failing_runner = make_failing_runner(stdout="network offline")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._publish_crates(
            plan,
            preparation,
            runner=failing_runner,
            options=publish._PublishExecutionOptions(live=False, allow_dirty=True),
        )

    message = str(excinfo.value)
    assert "cargo publish failed for crate" in message
    assert "network offline" in message
