"""Phase dispatch under both crates.io index-miss diagnostics.

Exercises ``_package_publishable_crates`` and ``_publish_crates`` through the
shared ``invoke_phase`` dispatcher for the version-selection and
absent-package diagnostic shapes. Each scenario runs against both Cargo
phases: the fatal path when the flag is unset, the fatal path when the
dependency is outside the publish plan, and the fatal path when the
dependency appears later in the publish order.
"""

import dataclasses as dc
import logging
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

import pytest

from lading.commands import publish, publish_pipeline, publish_plan, publish_staging
from lading.commands.cargo_output_adapter import CargoIndexLookupFailure

from .conftest import (
    PhaseContext,
    invoke_phase,
    make_config,
    make_crate,
    make_dependency,
    make_dependency_chain,
    make_failing_runner,
    make_workspace,
    prepare_staging_root,
)
from .index_missing_outputs import (
    INDEX_MISSING_PACKAGE_STDERR_BETA,
    INDEX_MISSING_PACKAGE_STDERR_EXTERNAL,
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_EXTERNAL,
)

# ``pytest.mark.ParameterSet`` names a ``MarkDecorator`` constructor rather
# than a type, so the element type is left to inference.
_PHASE_IDS = [
    pytest.param("package", publish.PublishPreflightError, id="packaging"),
    pytest.param("publish", publish_pipeline.PublishError, id="publish-dry-run"),
]
_BETA_DIAGNOSTICS = [
    pytest.param(
        ("version-selection", INDEX_MISSING_STDERR_BETA), id="version-selection"
    ),
    pytest.param(
        ("absent-package", INDEX_MISSING_PACKAGE_STDERR_BETA), id="absent-package"
    ),
]
_EXTERNAL_DIAGNOSTICS = [
    pytest.param(
        ("version-selection", INDEX_MISSING_STDERR_EXTERNAL), id="version-selection"
    ),
    pytest.param(
        ("absent-package", INDEX_MISSING_PACKAGE_STDERR_EXTERNAL), id="absent-package"
    ),
]
_HYPHENATED_STDERR = {
    "version-selection": (
        "error: failed to prepare local package for uploading\n"
        "Caused by:\n"
        '  failed to select a version for the requirement `my-crate = "^0.1.0"`\n'
        "  location searched: crates.io index\n"
    ),
    "absent-package": (
        "error: failed to prepare local package for uploading\n"
        "Caused by:\n"
        "  no matching package named `my-crate` found\n"
        "  location searched: crates.io index\n"
    ),
}


@pytest.mark.parametrize(("phase_name", "exc_type"), _PHASE_IDS)
@pytest.mark.parametrize("diagnostic", _BETA_DIAGNOSTICS)
def test_missing_dep_later_in_publish_order_raises(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    phase_name: str,
    exc_type: type[Exception],
    diagnostic: tuple[str, str],
) -> None:
    """A planned dependency must precede the failing crate to be tolerated."""
    _shape, beta_stderr = diagnostic
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
                runner=make_failing_runner(stderr=beta_stderr),
                options=publish_pipeline._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=True,
                ),
            ),
        )


def test_missing_dep_in_plan_allows_cargo_name_normalization(
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
@pytest.mark.parametrize("diagnostic", _BETA_DIAGNOSTICS)
def test_missing_dep_in_plan_without_flag_raises(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    phase_name: str,
    exc_type: type[Exception],
    expected_fragment: str,
    diagnostic: tuple[str, str],
) -> None:
    """Without the override, either index lookup error remains fatal."""
    _shape, beta_stderr = diagnostic
    plan, preparation, _staging_root = publish_plan_and_prep

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del env, command, echo_stdout
        crate_name = "" if cwd is None else cwd.name
        if crate_name == "beta":
            return (1, "", beta_stderr)
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
    assert "unpublished workspace dependency override" in message, (
        "the fatal error must point at the unset unpublished-workspace-deps flag"
    )


@pytest.mark.parametrize(("phase_name", "exc_type"), _PHASE_IDS)
@pytest.mark.parametrize("diagnostic", _EXTERNAL_DIAGNOSTICS)
def test_missing_dep_not_in_plan_raises(
    tmp_path: Path,
    phase_name: str,
    exc_type: type[Exception],
    diagnostic: tuple[str, str],
) -> None:
    """The missing dependency must belong to the publish plan to be tolerated."""
    _shape, external_stderr = diagnostic
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
                runner=make_failing_runner(stderr=external_stderr),
                options=publish_pipeline._PublishExecutionOptions(
                    live=False,
                    allow_dirty=True,
                    allow_unpublished_workspace_deps=True,
                ),
            ),
        )

    message = str(excinfo.value)
    assert "not part of the current publish plan" in message, (
        "an out-of-plan missing dependency must stay fatal with that reason"
    )


@pytest.mark.parametrize("phase_name", ["package", "publish"])
@pytest.mark.parametrize("shape", sorted(_HYPHENATED_STDERR))
def test_hyphenated_dep_in_plan_matches_with_canonicalisation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    phase_name: str,
    shape: str,
) -> None:
    """Hyphenated cargo output name matches underscore manifest name."""
    caplog.set_level(logging.WARNING, logger=publish_pipeline.LOGGER.name)
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
    stderr = _HYPHENATED_STDERR[shape]

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del command, env, echo_stdout
        if cwd is not None and cwd.name == "dependent":
            return (1, "", stderr)
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

    assert any("my-crate" in message for message in caplog.messages), (
        "the hyphenated cargo name must be reported in the downgrade warning"
    )
