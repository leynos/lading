"""Unit tests for publish crate packaging workflow."""

from __future__ import annotations

import logging
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

import pytest

from lading.commands import publish

from .conftest import (
    make_config,
    make_dependency_chain,
    make_workspace,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _prepare_staging_root(plan: publish.PublishPlan, base_dir: Path) -> Path:
    """Create a staged workspace tree matching ``plan`` under ``base_dir``."""
    staging_root = base_dir / "staging" / plan.workspace_root.name
    for crate in plan.publishable:
        relative_root = crate.root_path.relative_to(plan.workspace_root)
        (staging_root / relative_root).mkdir(parents=True, exist_ok=True)
    return staging_root


@pytest.fixture
def publish_plan_and_prep(
    tmp_path: Path,
) -> tuple[publish.PublishPlan, publish.PublishPreparation, Path]:
    """Provide a publish plan, preparation object, and staging root."""
    workspace_root = tmp_path / "workspace"
    crates = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, *crates), make_config()
    )
    staging_root = _prepare_staging_root(plan, tmp_path)
    preparation = publish.PublishPreparation(
        staging_root=staging_root,
        copied_readmes=(),
    )
    return plan, preparation, staging_root


class CallTrackingRunner:
    """Track command invocations while returning successful results."""

    def __init__(self) -> None:
        """Initialise the runner with an empty call log."""
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Record the invocation and return a successful result."""
        del env
        self.calls.append((tuple(command), cwd))
        return 0, "", ""


def make_failing_runner(
    stdout: str = "", stderr: str = ""
) -> cabc.Callable[
    [cabc.Sequence[str]], tuple[int, str, str]
]:  # pragma: no cover - simple factory
    """Return a runner that always fails with exit code 1."""

    def _runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del command, cwd, env
        return 1, stdout, stderr

    return _runner


def test_package_publishable_crates_runs_in_plan_order(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Cargo package is invoked for every publishable crate in plan order."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish._package_publishable_crates(plan, preparation, runner=runner)

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [(("cargo", "package"), root) for root in expected_roots], (
        "cargo package should run once per publishable crate in order"
    )


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
            runner=tracked_runner,
        )

    assert calls == ["cargo package"]
    assert "cargo package failed for crate alpha" in str(excinfo.value)
    assert "packaging failed" in str(excinfo.value)


def test_package_publishable_crates_reports_stdout_on_failure(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Failure details fall back to stdout when stderr is empty."""
    plan, preparation, _staging_root = publish_plan_and_prep
    stdout_failure = make_failing_runner(stdout="stdout failure details")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._package_publishable_crates(
            plan,
            preparation,
            runner=stdout_failure,
        )

    message = str(excinfo.value)
    assert "stdout failure details" in message
    assert "cargo package failed for crate alpha" in message


def test_package_publishable_crates_prefers_stderr_over_stdout(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Error detail prefers stderr when both streams are populated."""
    plan, preparation, _staging_root = publish_plan_and_prep
    both_populated = make_failing_runner(stdout="stdout detail", stderr="stderr detail")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._package_publishable_crates(
            plan,
            preparation,
            runner=both_populated,
        )

    message = str(excinfo.value)
    assert "stderr detail" in message
    assert "stdout detail" not in message


def test_publish_crates_run_dry_run_in_order(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cargo publish --dry-run runs for each crate in publish order."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish._publish_crates(plan, preparation, runner=runner, live=False)

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [
        (("cargo", "publish", "--dry-run"), root) for root in expected_roots
    ]
    # Ensure we emit a helpful info log for the publish phase.
    assert any("cargo publish" in message for message in caplog.messages)


def test_publish_crates_run_live_without_dry_run(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
) -> None:
    """Live mode omits the --dry-run flag when publishing crates."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish._publish_crates(plan, preparation, runner=runner, live=True)

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [(("cargo", "publish"), root) for root in expected_roots]


def test_publish_crates_continue_when_version_already_uploaded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Already-published versions log a warning and continue."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    workspace_root = tmp_path / "workspace"
    alpha, beta, _gamma = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, alpha, beta), make_config()
    )
    staging_root = _prepare_staging_root(plan, tmp_path)
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

    publish._publish_crates(plan, preparation, runner=runner, live=False)

    assert calls == ["alpha", "beta"]
    assert any("already published" in message for message in caplog.messages)


def test_publish_crates_raise_on_failure(
    publish_plan_and_prep: tuple[
        publish.PublishPlan, publish.PublishPreparation, Path
    ],
) -> None:
    """Unexpected cargo publish failures abort the workflow."""
    plan, preparation, _staging_root = publish_plan_and_prep
    failing_runner = make_failing_runner(stdout="network offline")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._publish_crates(plan, preparation, runner=failing_runner, live=False)

    message = str(excinfo.value)
    assert "cargo publish failed for crate" in message
    assert "network offline" in message
