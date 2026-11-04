"""Unit tests for publish-phase dispatch and dry-run phase wrapping.

Exercises the shared ``invoke_phase`` dispatcher and the dry-run phase
wrappers. Index-miss classification scenarios live in
``test_phase_dispatch_index_miss`` to keep both modules focused.
"""

import logging
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

import pytest

from lading.commands import publish, publish_pipeline, publish_plan, publish_staging

from .conftest import (
    CallTrackingRunner,
    PhaseContext,
    _warning_records,
    invoke_phase,
)
from .index_missing_outputs import (
    INDEX_MISSING_PACKAGE_STDERR_BETA,
    INDEX_MISSING_STDERR_BETA,
)

_SHAPES_AND_STDERR = (
    ("version-selection", INDEX_MISSING_STDERR_BETA),
    ("absent-package", INDEX_MISSING_PACKAGE_STDERR_BETA),
)
# One case per (phase, diagnostic shape) pair keeps the test signature small
# enough to satisfy the advisory argument-count rule while preserving a
# readable id for each combination.
_PHASE_DIAGNOSTIC_CASES = [
    pytest.param((phase_name, shape, stderr), id=f"{phase_name}-{shape}")
    for phase_name in ("package", "publish")
    for shape, stderr in _SHAPES_AND_STDERR
]


@pytest.mark.parametrize("case", _PHASE_DIAGNOSTIC_CASES)
def test_missing_dep_in_plan_and_flag_continues(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    case: tuple[str, str, str],
) -> None:
    """Flag downgrades either missing-index shape to a warning and proceeds."""
    phase_name, _shape, beta_stderr = case
    caplog.set_level(logging.WARNING, logger=publish_pipeline.LOGGER.name)
    plan, preparation, _staging_root = publish_plan_and_prep
    calls: list[str] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        del env, command, echo_stdout
        crate_name = "" if cwd is None else cwd.name
        calls.append(crate_name)
        if crate_name == "beta":
            return (1, "", beta_stderr)
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

    assert calls == ["alpha", "beta", "gamma"], (
        "the downgrade path must still package every crate in plan order"
    )
    assert _warning_records(caplog) == snapshot, (
        "the missing dependency must be downgraded to the recorded warning"
    )


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
    runner = CallTrackingRunner()

    state = publish_pipeline._PublicationPipelineState(plan, preparation, options)
    publish_pipeline._package_publishable_crates(state, runner=runner)
    publish_pipeline._publish_crates(state, runner=runner)

    assert len(calls) == 2, (
        "each wrapper must dispatch through the shared helper exactly once"
    )
    assert calls[0]["action"] is publish_pipeline._package_crate, (
        "the packaging wrapper must dispatch the package action first"
    )
    assert calls[1]["action"] is publish_pipeline._publish_crate, (
        "the publishing wrapper must dispatch the publish action"
    )
    assert calls[0]["runner"] is runner, (
        "the injected runner must be forwarded to the packaging dispatch"
    )
    assert calls[1]["runner"] is runner, (
        "the injected runner must be forwarded to the publishing dispatch"
    )


def test_run_dry_run_phase_normalises_packaging_preparation_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A packaging preparation error becomes a preflight error."""
    caplog.set_level(logging.ERROR, logger=publish_pipeline.LOGGER.name)
    failure = publish_pipeline.PublishPreparationError("staging failed")

    with pytest.raises(
        publish.PublishPreflightError, match="staging failed"
    ) as excinfo:
        publish_pipeline._run_dry_run_phase(
            "packaging",
            lambda: (_ for _ in ()).throw(failure),
        )

    assert excinfo.value.__cause__ is failure, (
        "the preflight error must chain the original preparation failure"
    )
    assert caplog.messages == ["Dry-run pipeline: packaging phase failed"], (
        "the packaging failure must be logged with the phase named"
    )


def test_run_dry_run_phase_succeeds_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful phase invokes its action without emitting an error."""
    caplog.set_level(logging.ERROR, logger=publish_pipeline.LOGGER.name)
    calls: list[str] = []

    publish_pipeline._run_dry_run_phase("packaging", lambda: calls.append("ran"))

    assert calls == ["ran"], "the phase action must run exactly once"
    assert caplog.messages == [], "a successful phase must emit no error-level messages"


def test_run_dry_run_phase_reraises_publish_preflight_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A publish preflight error retains its type and traceback."""
    caplog.set_level(logging.ERROR, logger=publish_pipeline.LOGGER.name)
    failure = publish.PublishPreflightError("publish failed")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_pipeline._run_dry_run_phase(
            "publish",
            lambda: (_ for _ in ()).throw(failure),
        )

    assert excinfo.value is failure, (
        "a publish preflight failure must propagate unchanged"
    )
    assert caplog.messages == ["Dry-run pipeline: publish phase failed"], (
        "the publish failure must be logged with the phase named"
    )
