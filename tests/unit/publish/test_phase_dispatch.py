"""Unit tests for publish-phase dispatch behaviour under index-missing-version failures.

Exercises ``lading.commands.publish_pipeline._package_publishable_crates`` and
``lading.commands.publish_pipeline._publish_crates`` through the shared
``invoke_phase`` dispatcher from ``tests.unit.publish.conftest``.

Three parametrised scenarios (package phase and publish-dry-run phase) verify:
- the downgrade path when ``--allow-unpublished-workspace-deps`` is set and
  the missing dependency is in the publish plan,
- the fatal path when the flag is unset, and
- the fatal path when the missing dependency is outside the publish plan.

Warning messages are verified via snapshot assertions backed by syrupy.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

import pytest

from lading.commands import publish, publish_pipeline, publish_plan, publish_staging
from lading.commands.cargo_output_adapter import CargoIndexLookupFailure

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_EXTERNAL,
    PhaseContext,
    _warning_records,
    invoke_phase,
    make_config,
    make_crate,
    make_dependency,
    make_dependency_chain,
    make_failing_runner,
    make_workspace,
    prepare_staging_root,
)

_PHASE_IDS: list[pytest.mark.ParameterSet] = [
    pytest.param("package", publish.PublishPreflightError, id="packaging"),
    pytest.param("publish", publish_pipeline.PublishError, id="publish-dry-run"),
]


@pytest.mark.parametrize("phase_name", ["package", "publish"])
def test_missing_dep_in_plan_and_flag_continues(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
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
            options=publish_pipeline._PublishExecutionOptions(
                live=False,
                allow_dirty=True,
                allow_unpublished_workspace_deps=True,
            ),
        ),
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert _warning_records(caplog) == snapshot(name=phase_name)


@pytest.mark.parametrize(("phase_name", "exc_type"), _PHASE_IDS)
def test_missing_dep_later_in_publish_order_raises(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    phase_name: str,
    exc_type: type[Exception],
) -> None:
    """A planned dependency must precede the failing crate to be tolerated."""
    original_plan, _preparation, staging_root = publish_plan_and_prep
    alpha, beta, gamma = original_plan.publishable
    plan = dc.replace(original_plan, publishable=(beta, alpha, gamma))
    preparation = publish_staging.PublishPreparation(
        staging_root=prepare_staging_root(plan, staging_root.parent.parent),
    )

    with pytest.raises(exc_type, match=r"appears after .* in publish order"):
        invoke_phase(
            phase_name,
            PhaseContext(
                plan=plan,
                preparation=preparation,
                runner=make_failing_runner(stderr=INDEX_MISSING_STDERR_BETA),
                options=publish_pipeline._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=True,
                ),
            ),
        )


def test_missing_dep_in_plan_allows_cargo_name_normalisation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cargo-reported underscores match hyphenated publish-plan crate names."""
    caplog.set_level(logging.WARNING)
    workspace_root = tmp_path / "workspace"
    alpha = make_crate(workspace_root, "alpha-crate")
    beta = make_crate(workspace_root, "beta")
    plan = publish.plan_publication(
        make_workspace(workspace_root, alpha, beta), make_config()
    )
    failure = CargoIndexLookupFailure(
        crate_name="beta",
        subcommand="package",
        exit_code=1,
        stdout="",
        stderr=(
            "error: failed to prepare local package for uploading\n"
            "Caused by:\n"
            "  failed to select a version for the requirement "
            '`alpha_crate = "^1"`\n'
            "  location searched: crates.io index\n"
        ),
        missing_dependency_name="alpha_crate",
    )

    publish_pipeline._handle_index_missing_version(
        failure,
        plan=plan,
        options=publish_pipeline._PublishExecutionOptions(
            live=False,
            allow_dirty=True,
            allow_unpublished_workspace_deps=True,
        ),
    )
    assert any(
        "could not resolve sibling dependency alpha_crate" in message
        and "unpublished workspace dependency override is enabled" in message
        for message in caplog.messages
    ), "expected canonicalised in-plan dependency to be downgraded to a warning"


@pytest.mark.parametrize(
    ("phase_name", "exc_type", "expected_fragment"),
    [
        pytest.param("package", publish.PublishPreflightError, "alpha", id="packaging"),
        pytest.param(
            "publish",
            publish_pipeline.PublishError,
            "cargo publish failed for crate beta",
            id="publish-dry-run",
        ),
    ],
)
def test_missing_dep_in_plan_without_flag_raises(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
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

    with pytest.raises(exc_type, match=re.escape(expected_fragment)) as excinfo:
        invoke_phase(
            phase_name,
            PhaseContext(
                plan=plan,
                preparation=preparation,
                runner=runner,
                options=publish_pipeline._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=False,
                ),
            ),
        )

    message = str(excinfo.value)
    assert "unpublished workspace dependency override" in message


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
    preparation = publish_staging.PublishPreparation(
        staging_root=staging_root,
    )
    with pytest.raises(exc_type, match=r"external_crate") as excinfo:
        invoke_phase(
            phase_name,
            PhaseContext(
                plan=plan,
                preparation=preparation,
                runner=make_failing_runner(stderr=INDEX_MISSING_STDERR_EXTERNAL),
                options=publish_pipeline._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=True,
                ),
            ),
        )

    message = str(excinfo.value)
    assert "not part of the current publish plan" in message


@pytest.mark.parametrize("phase_name", ["package", "publish"])
def test_hyphenated_dep_in_plan_matches_with_canonicalisation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    phase_name: str,
) -> None:
    """Hyphenated cargo output name matches underscore manifest name."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    workspace_root = tmp_path / "workspace"
    dependency = make_crate(workspace_root, "my_crate")
    dependent = make_crate(
        workspace_root,
        "dependent",
        dependencies=(make_dependency("my_crate"),),
    )
    plan = publish.plan_publication(
        make_workspace(workspace_root, dependency, dependent), make_config()
    )
    staging_root = prepare_staging_root(plan, tmp_path)
    preparation = publish_staging.PublishPreparation(
        staging_root=staging_root,
    )
    hyphenated_stderr = (
        "error: failed to prepare local package for uploading\n"
        "Caused by:\n"
        '  failed to select a version for the requirement `my-crate = "^0.1.0"`\n'
        "  location searched: crates.io index\n"
    )

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del command, env
        if cwd is not None and cwd.name == "dependent":
            return (1, "", hyphenated_stderr)
        return (0, "", "")

    ctx = PhaseContext(
        plan=plan,
        preparation=preparation,
        runner=runner,
        options=publish_pipeline._PublishExecutionOptions(
            live=False,
            allow_dirty=True,
            allow_unpublished_workspace_deps=True,
        ),
    )

    invoke_phase(phase_name, ctx)

    assert any("my-crate" in message for message in caplog.messages)


def test_package_and_publish_dispatch_through_shared_helper(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both crate-iteration wrappers delegate to ``_for_each_publishable_crate``."""
    plan, preparation, _ = publish_plan_and_prep
    calls: list[dict[str, object]] = []

    def fake_for_each(
        state: publish_pipeline._PublicationPipelineState,
        *,
        runner: object,
        action: publish_pipeline._CrateAction,
    ) -> None:
        """Record each dispatch instead of iterating over crates."""
        calls.append({"state": state, "runner": runner, "action": action})

    monkeypatch.setattr(publish_pipeline, "_for_each_publishable_crate", fake_for_each)

    options = publish_pipeline._PublishExecutionOptions(live=False, allow_dirty=True)
    runner = object()

    publish_pipeline._package_publishable_crates(
        plan, preparation, options=options, runner=runner
    )
    publish_pipeline._publish_crates(plan, preparation, runner=runner, options=options)

    assert len(calls) == 2
    assert calls[0]["action"] is publish_pipeline._package_crate
    assert calls[1]["action"] is publish_pipeline._publish_crate
    assert calls[0]["runner"] is runner
    assert calls[1]["runner"] is runner
