"""Unit tests for publish crate packaging workflow.

Exercises the per-crate publication helpers and the interleaved live
pipeline introduced in :mod:`lading.commands.publish`:

- :func:`~lading.commands.publish_pipeline._package_crate` — packages one crate
  from the staged workspace via ``cargo package``.
- :func:`~lading.commands.publish_pipeline._publish_crate` — publishes one crate
  via ``cargo publish``, with ``--dry-run`` injected when not in live
  mode.
- :func:`~lading.commands.publish_pipeline._execute_live_publication_pipeline` —
  orchestrates the interleaved per-crate package-then-publish flow.

Test helpers
------------
``CallTrackingRunner``
    Records ``(command, cwd)`` pairs without executing real ``cargo``
    subprocesses, enabling post-call assertion of both the invoked
    command and the working directory.
``make_failing_runner``
    Returns a runner that yields a configurable non-zero exit code with
    injected stdout/stderr text, used to exercise failure branches.
``_SnapshotCase``
    Named tuple bundling the four parametrised fields for snapshot tests
    (helper function, execution options, expected exception type, and
    injected stderr text), keeping the test function's argument count
    within the four-parameter threshold.

Coverage
--------
- Correct ``cargo`` subcommand and ``cwd`` for each single-crate helper.
- ``PublishPreflightError`` raised on package failure with injected stderr.
- ``PublishError`` raised on publish failure with injected stdout.
- Already-published continuation: warning logged, no exception raised.
- Live pipeline interleaving: package then publish per crate in plan order.
- Live pipeline abort after a partial publish: earlier pairs complete first.
- Exact error-message formatting locked in via syrupy snapshot assertions.
"""

from __future__ import annotations

import collections.abc as cabc
import logging
import shutil
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

import pytest

from lading.commands import publish, publish_pipeline, publish_plan, publish_staging

from .conftest import (
    CallTrackingRunner,
    make_config,
    make_dependency_chain,
    make_failing_runner,
    make_workspace,
    prepare_staging_root,
)


class _SnapshotCase(typ.NamedTuple):
    fn: cabc.Callable[..., None]
    options: publish_pipeline._PublishExecutionOptions
    exc_type: type[Exception]
    stderr_text: str


class _FailureCase(typ.NamedTuple):
    fn: cabc.Callable[..., None]
    live: bool
    exc_type: type[Exception]
    runner_kwargs: dict[str, str]
    subcommand: str
    output_fragment: str


class _PackagingFailureDetail(typ.NamedTuple):
    stdout: str
    stderr: str
    expected_in_message: str
    not_expected_in_message: str | None


def _assert_packaging_failure_message_contains(
    plan_and_prep: tuple[publish_plan.PublishPlan, publish_staging.PublishPreparation],
    runner: cabc.Callable[..., tuple[int, str, str]],
    expected_in_message: str,
    not_expected_in_message: str | None = None,
) -> None:
    """Assert that packaging failure produces expected error message content."""
    plan, preparation = plan_and_prep

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_pipeline._package_publishable_crates(
            plan,
            preparation,
            options=publish_pipeline._PublishExecutionOptions(
                live=False, allow_dirty=True
            ),
            runner=runner,
        )

    message = str(excinfo.value)
    assert "cargo package failed for crate alpha" in message
    assert expected_in_message in message
    if not_expected_in_message is not None:
        assert not_expected_in_message not in message


def test_package_publishable_crates_runs_in_plan_order(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cargo package is invoked for every publishable crate in plan order."""
    caplog.set_level(logging.INFO, logger=publish_pipeline.LOGGER.name)
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish_pipeline._package_publishable_crates(
        plan,
        preparation,
        options=publish_pipeline._PublishExecutionOptions(live=False, allow_dirty=True),
        runner=runner,
    )

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [
        (("cargo", "package", "--allow-dirty"), root) for root in expected_roots
    ], "cargo package should run once per publishable crate in order"
    assert caplog.messages == [
        "Running cargo package for crate alpha",
        "Successfully packaged crate alpha",
        "Running cargo package for crate beta",
        "Successfully packaged crate beta",
        "Running cargo package for crate gamma",
        "Successfully packaged crate gamma",
    ]


@pytest.mark.parametrize(
    ("fn", "options", "expected_cmd"),
    [
        pytest.param(
            publish_pipeline._package_crate,
            publish_pipeline._PublishExecutionOptions(live=False, allow_dirty=True),
            ("cargo", "package", "--allow-dirty"),
            id="package",
        ),
        pytest.param(
            publish_pipeline._publish_crate,
            publish_pipeline._PublishExecutionOptions(live=True, allow_dirty=True),
            ("cargo", "publish", "--allow-dirty"),
            id="publish",
        ),
    ],
)
def test_single_crate_helper_invokes_correct_cargo_command(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    fn: cabc.Callable[..., None],
    options: publish_pipeline._PublishExecutionOptions,
    expected_cmd: tuple[str, ...],
) -> None:
    """Each single-crate helper invokes the correct cargo subcommand."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()
    crate = plan.publishable[1]
    state = publish_pipeline._PublicationPipelineState(plan, preparation, options)

    fn(
        crate,
        state,
        runner=runner,
    )

    expected_root = staging_root / crate.root_path.relative_to(plan.workspace_root)
    assert runner.calls == [(expected_cmd, expected_root)]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _FailureCase(
                fn=publish_pipeline._package_crate,
                live=False,
                exc_type=publish.PublishPreflightError,
                runner_kwargs={"stderr": "packaging failed"},
                subcommand="package",
                output_fragment="packaging failed",
            ),
            id="package",
        ),
        pytest.param(
            _FailureCase(
                fn=publish_pipeline._publish_crate,
                live=True,
                exc_type=publish_pipeline.PublishError,
                runner_kwargs={"stdout": "network offline"},
                subcommand="publish",
                output_fragment="network offline",
            ),
            id="publish",
        ),
    ],
)
def test_crate_helper_raises_on_failure(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    case: _FailureCase,
) -> None:
    """Each single-crate helper raises the correct exception type on failure."""
    plan, preparation, _staging_root = publish_plan_and_prep
    state = publish_pipeline._PublicationPipelineState(
        plan,
        preparation,
        publish_pipeline._PublishExecutionOptions(live=case.live, allow_dirty=True),
    )

    with pytest.raises(case.exc_type) as excinfo:
        case.fn(
            plan.publishable[0],
            state,
            runner=make_failing_runner(**case.runner_kwargs),
        )

    message = str(excinfo.value)
    assert f"cargo {case.subcommand} failed for crate alpha" in message, (
        f"expected failure message to include cargo {case.subcommand} "
        "context for crate alpha"
    )
    assert case.output_fragment in message, (
        f"expected failure message to include output fragment {case.output_fragment!r}"
    )


def test_package_publishable_crates_stops_on_failure(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
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
        publish_pipeline._package_publishable_crates(
            plan,
            preparation,
            options=publish_pipeline._PublishExecutionOptions(
                live=False, allow_dirty=True
            ),
            runner=tracked_runner,
        )

    assert calls == ["cargo package --allow-dirty"]
    assert "cargo package failed for crate alpha" in str(excinfo.value)
    assert "packaging failed" in str(excinfo.value)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _PackagingFailureDetail(
                stdout="stdout failure details",
                stderr="",
                expected_in_message="stdout failure details",
                not_expected_in_message=None,
            ),
            id="stdout_fallback_when_stderr_empty",
        ),
        pytest.param(
            _PackagingFailureDetail(
                stdout="stdout detail",
                stderr="stderr detail",
                expected_in_message="stderr detail",
                not_expected_in_message="stdout detail",
            ),
            id="stderr_takes_precedence_over_stdout",
        ),
    ],
)
def test_package_publishable_crates_reports_failure_detail(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    case: _PackagingFailureDetail,
) -> None:
    """Failure details fall back to stdout or prefer populated stderr."""
    plan_and_prep = publish_plan_and_prep[:2]
    runner = make_failing_runner(stdout=case.stdout, stderr=case.stderr)

    _assert_packaging_failure_message_contains(
        plan_and_prep,
        runner,
        expected_in_message=case.expected_in_message,
        not_expected_in_message=case.not_expected_in_message,
    )


def test_publish_crates_run_dry_run_in_order(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cargo publish --dry-run runs for each crate in publish order."""
    caplog.set_level(logging.INFO, logger=publish_pipeline.LOGGER.name)
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish_pipeline._publish_crates(
        plan,
        preparation,
        runner=runner,
        options=publish_pipeline._PublishExecutionOptions(live=False, allow_dirty=True),
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
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
) -> None:
    """Live mode omits the --dry-run flag when publishing crates."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish_pipeline._publish_crates(
        plan,
        preparation,
        runner=runner,
        options=publish_pipeline._PublishExecutionOptions(live=True, allow_dirty=True),
    )

    expected_roots = [
        staging_root / crate.root_path.relative_to(plan.workspace_root)
        for crate in plan.publishable
    ]
    assert runner.calls == [
        (("cargo", "publish", "--allow-dirty"), root) for root in expected_roots
    ]


def test_publish_crate_continues_when_version_already_uploaded(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The single-crate publish helper keeps already-published handling."""
    caplog.set_level(logging.WARNING, logger=publish_pipeline.LOGGER.name)
    plan, preparation, _staging_root = publish_plan_and_prep

    def already_uploaded_runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del command, cwd, env
        return 101, "", "error: crate version `alpha v0.1.0` is already uploaded"

    state = publish_pipeline._PublicationPipelineState(
        plan,
        preparation,
        publish_pipeline._PublishExecutionOptions(live=True, allow_dirty=True),
    )

    publish_pipeline._publish_crate(
        plan.publishable[0],
        state,
        runner=already_uploaded_runner,
    )

    assert any("already published" in message for message in caplog.messages)


@pytest.mark.parametrize(
    "live",
    [pytest.param(False, id="dry-run"), pytest.param(True, id="live")],
)
def test_publish_crates_continue_when_version_already_uploaded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, *, live: bool
) -> None:
    """Already-published versions log a warning and continue."""
    caplog.set_level(logging.WARNING, logger=publish_pipeline.LOGGER.name)
    workspace_root = tmp_path / "workspace"
    alpha, beta, _gamma = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, alpha, beta), make_config()
    )
    staging_root = prepare_staging_root(plan, tmp_path)
    preparation = publish_staging.PublishPreparation(
        staging_root=staging_root,
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

    publish_pipeline._publish_crates(
        plan,
        preparation,
        runner=runner,
        options=publish_pipeline._PublishExecutionOptions(live=live, allow_dirty=True),
    )

    assert calls == ["alpha", "beta"]
    assert any("already published" in message for message in caplog.messages)


def test_execute_live_publication_pipeline_interleaves_package_and_publish(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
) -> None:
    """Live publication packages and publishes each crate before continuing."""
    plan, preparation, staging_root = publish_plan_and_prep
    runner = CallTrackingRunner()

    publish_pipeline._execute_live_publication_pipeline(
        plan,
        preparation,
        options=publish_pipeline._PublishExecutionOptions(live=True, allow_dirty=True),
        runner=runner,
    )

    expected_calls: list[tuple[tuple[str, ...], Path]] = []
    for crate in plan.publishable:
        crate_root = staging_root / crate.root_path.relative_to(plan.workspace_root)
        expected_calls.extend([
            (("cargo", "package", "--allow-dirty"), crate_root),
            (("cargo", "publish", "--allow-dirty"), crate_root),
        ])

    assert runner.calls == expected_calls


def test_execute_live_publication_pipeline_stops_after_partial_publish(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
) -> None:
    """A later live failure leaves earlier publish attempts completed."""
    plan, preparation, staging_root = publish_plan_and_prep
    beta_root = staging_root / plan.publishable[1].root_path.relative_to(
        plan.workspace_root
    )
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env
        normalised = tuple(command)
        calls.append((normalised, cwd))
        if normalised[:2] == ("cargo", "package") and cwd == beta_root:
            return 1, "", "packaging failed"
        return 0, "", ""

    with pytest.raises(publish.PublishPreflightError):
        publish_pipeline._execute_live_publication_pipeline(
            plan,
            preparation,
            options=publish_pipeline._PublishExecutionOptions(
                live=True, allow_dirty=True
            ),
            runner=runner,
        )

    alpha_root = staging_root / plan.publishable[0].root_path.relative_to(
        plan.workspace_root
    )
    assert calls == [
        (("cargo", "package", "--allow-dirty"), alpha_root),
        (("cargo", "publish", "--allow-dirty"), alpha_root),
        (("cargo", "package", "--allow-dirty"), beta_root),
    ]


def test_execute_live_publication_pipeline_wraps_preparation_errors(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Preparation failures surface as ``PublishPreflightError`` for the caller."""
    caplog.set_level(logging.ERROR, logger=publish_pipeline.LOGGER.name)
    plan, preparation, staging_root = publish_plan_and_prep
    # Remove the staged tree for the second crate so resolving its root fails
    # mid-pipeline, after the first crate has packaged and published cleanly.
    beta_root = staging_root / plan.publishable[1].root_path.relative_to(
        plan.workspace_root
    )
    shutil.rmtree(beta_root)
    runner = CallTrackingRunner()

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_pipeline._execute_live_publication_pipeline(
            plan,
            preparation,
            options=publish_pipeline._PublishExecutionOptions(
                live=True, allow_dirty=True
            ),
            runner=runner,
        )

    assert isinstance(excinfo.value.__cause__, publish_staging.PublishPreparationError)
    assert str(beta_root) in str(excinfo.value)
    alpha_root = staging_root / plan.publishable[0].root_path.relative_to(
        plan.workspace_root
    )
    assert runner.calls == [
        (("cargo", "package", "--allow-dirty"), alpha_root),
        (("cargo", "publish", "--allow-dirty"), alpha_root),
    ], (
        "expected alpha to be packaged and published "
        "(cargo package + cargo publish) in alpha_root before beta "
        f"preparation aborted; alpha_root={alpha_root!s}, calls={runner.calls!r}"
    )
    assert any(
        "Live pipeline: aborted on crate beta" in message for message in caplog.messages
    )


def test_publish_crates_raise_on_failure(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
) -> None:
    """Unexpected cargo publish failures abort the workflow."""
    plan, preparation, _staging_root = publish_plan_and_prep
    failing_runner = make_failing_runner(stdout="network offline")

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish_pipeline._publish_crates(
            plan,
            preparation,
            runner=failing_runner,
            options=publish_pipeline._PublishExecutionOptions(
                live=False, allow_dirty=True
            ),
        )

    message = str(excinfo.value)
    assert "cargo publish failed for crate" in message
    assert "network offline" in message


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SnapshotCase(
                fn=publish_pipeline._package_crate,
                options=publish_pipeline._PublishExecutionOptions(
                    live=False, allow_dirty=True
                ),
                exc_type=publish.PublishPreflightError,
                stderr_text="packaging failed",
            ),
            id="package",
        ),
        pytest.param(
            _SnapshotCase(
                fn=publish_pipeline._publish_crate,
                options=publish_pipeline._PublishExecutionOptions(
                    live=True, allow_dirty=True
                ),
                exc_type=publish_pipeline.PublishError,
                stderr_text="publish failed",
            ),
            id="publish",
        ),
    ],
)
def test_crate_helper_error_message_snapshot(
    publish_plan_and_prep: tuple[
        publish_plan.PublishPlan, publish_staging.PublishPreparation, Path
    ],
    snapshot: SnapshotAssertion,
    case: _SnapshotCase,
) -> None:
    """Snapshot the error message raised by each single-crate helper on failure."""
    plan, preparation, _staging_root = publish_plan_and_prep
    state = publish_pipeline._PublicationPipelineState(plan, preparation, case.options)

    with pytest.raises(case.exc_type) as excinfo:
        case.fn(
            plan.publishable[0],
            state,
            runner=make_failing_runner(stdout="", stderr=case.stderr_text),
        )

    assert str(excinfo.value) == snapshot()
