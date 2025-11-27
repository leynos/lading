"""Unit tests for publish crate packaging workflow."""

from __future__ import annotations

import typing as typ

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


def test_package_publishable_crates_runs_in_plan_order(tmp_path: Path) -> None:
    """Cargo package is invoked for every publishable crate in plan order."""
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

    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def runner(
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env
        calls.append((tuple(command), cwd))
        return 0, "", ""

    publish._package_publishable_crates(plan, preparation, runner=runner)

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert calls == [(("cargo", "package"), root) for root in expected_roots], (
        "cargo package should run once per publishable crate in order"
    )


def test_package_publishable_crates_stops_on_failure(tmp_path: Path) -> None:
    """Failures during packaging abort the workflow with crate context."""
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

    def failing_runner(
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env, cwd
        calls.append(" ".join(command))
        return (1, "", "packaging failed")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._package_publishable_crates(
            plan,
            preparation,
            runner=failing_runner,
        )

    assert calls == ["cargo package"]
    assert "cargo package failed for crate alpha" in str(excinfo.value)
    assert "packaging failed" in str(excinfo.value)


def test_package_publishable_crates_reports_stdout_on_failure(tmp_path: Path) -> None:
    """Failure details fall back to stdout when stderr is empty."""
    workspace_root = tmp_path / "workspace"
    alpha, _beta, _gamma = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, alpha), make_config()
    )
    staging_root = _prepare_staging_root(plan, tmp_path)
    preparation = publish.PublishPreparation(
        staging_root=staging_root,
        copied_readmes=(),
    )

    def stdout_failure(
        command: typ.Sequence[str],
        *,
        cwd: Path | None = None,
        env: typ.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env, cwd
        return (1, "stdout failure details", "")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._package_publishable_crates(
            plan,
            preparation,
            runner=stdout_failure,
        )

    message = str(excinfo.value)
    assert "stdout failure details" in message
    assert "cargo package failed for crate alpha" in message
