"""Unit tests for handling index failures across publish phases."""

from __future__ import annotations

import logging
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

import pytest

from lading.commands import publish

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_EXTERNAL,
    PhaseContext,
    invoke_phase,
    make_config,
    make_dependency_chain,
    make_failing_runner,
    make_workspace,
    prepare_staging_root,
)

_PHASE_IDS: list[pytest.param] = [
    pytest.param("package", publish.PublishPreflightError, id="packaging"),
    pytest.param("publish", publish.PublishError, id="publish-dry-run"),
]


@pytest.mark.parametrize("phase_name", ["package", "publish"])
def test_missing_dep_in_plan_and_flag_continues(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    phase_name: str,
) -> None:
    """Flag downgrades the missing-index error to a warning and proceeds."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    plan, preparation, _staging_root = publish_plan_and_prep
    calls: list[str] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env, command
        crate_name = "" if cwd is None else cwd.name
        calls.append(crate_name)
        if crate_name == "beta":
            return (1, "", INDEX_MISSING_STDERR_BETA)
        return (0, "", "")

    invoke_phase(
        phase_name,
        PhaseContext(
            plan=plan,
            preparation=preparation,
            runner=runner,
            options=publish._PublishExecutionOptions(
                live=False,
                allow_dirty=True,
                allow_unpublished_workspace_deps=True,
            ),
        ),
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert any("alpha" in m and "beta" in m for m in caplog.messages)


@pytest.mark.parametrize(
    ("phase_name", "exc_type", "expected_fragment"),
    [
        pytest.param("package", publish.PublishPreflightError, "alpha", id="packaging"),
        pytest.param(
            "publish",
            publish.PublishError,
            "cargo publish failed for crate beta",
            id="publish-dry-run",
        ),
    ],
)
def test_missing_dep_in_plan_without_flag_raises(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    phase_name: str,
    exc_type: type[Exception],
    expected_fragment: str,
) -> None:
    """Without the override, the index lookup error remains fatal."""
    plan, preparation, _staging_root = publish_plan_and_prep

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env, command
        crate_name = "" if cwd is None else cwd.name
        if crate_name == "beta":
            return (1, "", INDEX_MISSING_STDERR_BETA)
        return (0, "", "")

    with pytest.raises(exc_type) as excinfo:
        invoke_phase(
            phase_name,
            PhaseContext(
                plan=plan,
                preparation=preparation,
                runner=runner,
                options=publish._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=False,
                ),
            ),
        )

    message = str(excinfo.value)
    assert expected_fragment in message
    assert "--allow-unpublished-workspace-deps" in message


@pytest.mark.parametrize(("phase_name", "exc_type"), _PHASE_IDS)
def test_missing_dep_not_in_plan_raises(
    tmp_path: Path,
    phase_name: str,
    exc_type: type[Exception],
) -> None:
    """The missing dependency must belong to the publish plan to be tolerated."""
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
    with pytest.raises(exc_type) as excinfo:
        invoke_phase(
            phase_name,
            PhaseContext(
                plan=plan,
                preparation=preparation,
                runner=make_failing_runner(stderr=INDEX_MISSING_STDERR_EXTERNAL),
                options=publish._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=True,
                ),
            ),
        )

    message = str(excinfo.value)
    assert "external_crate" in message
    assert "not part of the current publish plan" in message
