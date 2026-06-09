"""Snapshot tests for human-readable messages emitted by publish index handling.

Covers every distinct message path in
``lading.commands.publish_index_check._handle_index_missing_version``:

- rejection of ``--allow-unpublished-workspace-deps`` when combined with ``--live``,
- name-extraction failure (unparseable cargo stderr),
- in-plan downgrade (flag set, missing dep scheduled in this publish run),
- out-of-plan failure (missing dep not in the publish plan),
- flag-disabled failure (flag unset, dep in plan).

All message assertions use syrupy ``snapshot()`` comparisons rather than
substring matching to lock in the exact format for regression detection.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

import pytest

from lading.commands import publish

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_EXTERNAL,
    INDEX_MISSING_STDERR_UNPARSEABLE,
    CallTrackingRunner,
    _warning_records,
    make_config,
    make_dependency_chain,
    make_workspace,
)

_PIPELINE_INFO_MESSAGES = frozenset({
    "Publication mode: live (interleaved per-crate pipeline)",
    "Publication mode: dry-run (batched two-phase pipeline)",
    "Dry-run pipeline: packaging complete; starting publish phase",
    "Live pipeline: starting crate %s",
    "Live pipeline: completed crate %s",
})


class _IndexMissingCase(typ.NamedTuple):
    stderr: str
    allow_unpublished: bool


class _InPlanSnapshotCase(typ.NamedTuple):
    """Variable parts for in-plan fatal-path snapshot tests."""

    plan_transform: cabc.Callable[[publish.PublishPlan], publish.PublishPlan]
    stderr_transform: cabc.Callable[[str], str]


def _pipeline_info_records(
    caplog: pytest.LogCaptureFixture,
) -> tuple[tuple[str, tuple[object, ...]], ...]:
    """Return captured INFO records for publish pipeline operator messages."""
    return tuple(
        (record.msg, record.args)
        for record in caplog.records
        if record.levelno == logging.INFO and record.msg in _PIPELINE_INFO_MESSAGES
    )


def test_run_rejects_allow_unpublished_with_live(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Combining ``--live`` with the override flag is a hard error."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    workspace_root = tmp_path / "workspace"
    crates = make_dependency_chain(workspace_root)
    workspace = make_workspace(workspace_root, *crates)
    configuration = make_config()

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish.run(
            workspace_root,
            configuration,
            workspace,
            options=publish.PublishOptions(
                live=True,
                allow_unpublished_workspace_deps=True,
            ),
        )

    assert str(excinfo.value) == snapshot()
    assert caplog.messages == [
        (
            "Unpublished workspace dependency override is only valid in dry-run "
            "mode. Live publish requires all dependency packages to be "
            "available on crates.io before the dependent crate is published."
        )
    ]


def _handle_index_missing_version_message(
    plan: publish.PublishPlan,
    *,
    stderr: str,
    allow_unpublished_workspace_deps: bool,
    caplog: pytest.LogCaptureFixture,
) -> str:
    """Return the raised index-missing-version message for snapshot tests."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    invocation = publish._CargoInvocation(
        crate_name="beta",
        subcommand="package",
        output=(1, "", stderr),
    )

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._handle_index_missing_version(
            invocation,
            plan=plan,
            options=publish._PublishExecutionOptions(
                live=False,
                allow_dirty=True,
                allow_unpublished_workspace_deps=allow_unpublished_workspace_deps,
            ),
        )

    return str(excinfo.value)


def _snapshot_message(message: str) -> str:
    """Make blank diagnostic lines visible in Amber snapshots."""
    return message.replace("\n\n", "\n<blank>\n")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _IndexMissingCase(
                stderr=INDEX_MISSING_STDERR_UNPARSEABLE,
                allow_unpublished=True,
            ),
            id="name_extraction_failure",
        ),
        pytest.param(
            _IndexMissingCase(
                stderr=INDEX_MISSING_STDERR_BETA,
                allow_unpublished=False,
            ),
            id="flag_disabled",
        ),
    ],
)
def test_index_missing_version_message_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    case: _IndexMissingCase,
) -> None:
    """Snapshot the fatal message and warning for index-missing-version failures."""
    plan, _preparation, _staging_root = publish_plan_and_prep

    message = _handle_index_missing_version_message(
        plan,
        stderr=case.stderr,
        allow_unpublished_workspace_deps=case.allow_unpublished,
        caplog=caplog,
    )

    assert _snapshot_message(message) == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")


def test_index_missing_in_plan_downgrade_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the warning emitted when the flag downgrades a failure to a warning."""
    caplog.set_level(logging.INFO)
    plan, _preparation, _staging_root = publish_plan_and_prep
    invocation = publish._CargoInvocation(
        crate_name="beta",
        subcommand="package",
        output=(1, "", INDEX_MISSING_STDERR_BETA),
    )

    # Must not raise - the success/downgrade path returns without raising.
    publish._handle_index_missing_version(
        invocation,
        plan=plan,
        options=publish._PublishExecutionOptions(
            live=False,
            allow_dirty=True,
            allow_unpublished_workspace_deps=True,
        ),
    )

    assert _warning_records(caplog) == snapshot(name="warning")
    assert any(
        "Downgraded cargo package failure for crate beta" in message
        and "dependency alpha (index 0) is part of the publish plan" in message
        for message in caplog.messages
    )


def test_index_missing_out_of_plan_message_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the fatal message and warning for out-of-plan dependencies."""
    workspace_root = tmp_path / "workspace"
    alpha, beta, _gamma = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, alpha, beta), make_config()
    )

    message = _handle_index_missing_version_message(
        plan,
        stderr=INDEX_MISSING_STDERR_EXTERNAL,
        allow_unpublished_workspace_deps=True,
        caplog=caplog,
    )

    assert _snapshot_message(message) == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _InPlanSnapshotCase(
                plan_transform=lambda plan: dc.replace(
                    plan,
                    publishable=(
                        plan.publishable[1],
                        plan.publishable[0],
                        *plan.publishable[2:],
                    ),
                ),
                stderr_transform=lambda stderr: stderr,
            ),
            id="out_of_order",
        ),
        pytest.param(
            _InPlanSnapshotCase(
                plan_transform=lambda plan: plan,
                stderr_transform=lambda stderr: stderr.replace("`alpha =", "`beta ="),
            ),
            id="self_dependency",
        ),
    ],
)
def test_index_missing_in_plan_fatal_message_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    case: _InPlanSnapshotCase,
) -> None:
    """Snapshot the fatal message and warning for in-plan dependency failures."""
    original_plan, _preparation, _staging_root = publish_plan_and_prep
    plan = case.plan_transform(original_plan)
    stderr = case.stderr_transform(INDEX_MISSING_STDERR_BETA)

    message = _handle_index_missing_version_message(
        plan,
        stderr=stderr,
        allow_unpublished_workspace_deps=True,
        caplog=caplog,
    )

    assert _snapshot_message(message) == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(
            publish._PublishExecutionOptions(live=False, allow_dirty=True),
            id="dry_run",
        ),
        pytest.param(
            publish._PublishExecutionOptions(live=True, allow_dirty=True),
            id="live",
        ),
    ],
)
def test_pipeline_info_log_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    options: publish._PublishExecutionOptions,
) -> None:
    """Snapshot pipeline selector and progression logs for each mode."""
    caplog.set_level(logging.INFO, logger="lading.commands.publish")
    plan, preparation, _staging_root = publish_plan_and_prep

    publish._dispatch_publication(
        plan,
        preparation,
        options=options,
        runner=CallTrackingRunner(),
    )

    assert _pipeline_info_records(caplog) == snapshot()


@pytest.mark.parametrize(
    "stderr_marker",
    [
        pytest.param("error: crate version `0.1.0` is already uploaded", id="uploaded"),
        pytest.param(
            "error: crate beta@0.1.0 already exists on crates.io index",
            id="index-exists",
        ),
    ],
)
def test_already_published_warning_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
    stderr_marker: str,
) -> None:
    """The already-published warning format is locked by snapshot.

    Issue #73: `_handle_publish_result` downgrades cargo registry exit code
    101 with an already-published marker to a WARNING and continues; the
    exact message was previously unconstrained.
    """
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
    workspace_root = tmp_path / "workspace"
    crates = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, *crates), make_config()
    )
    beta = next(crate for crate in plan.publishable if crate.name == "beta")
    invocation = publish._CargoInvocation(
        crate_name="beta",
        subcommand="publish",
        output=(101, "", stderr_marker),
    )

    publish._handle_publish_result(
        invocation,
        beta,
        plan,
        publish._PublishExecutionOptions(live=False, allow_dirty=True),
    )

    assert _warning_records(caplog) == snapshot()
