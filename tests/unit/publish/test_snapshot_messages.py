"""Snapshot tests for publish index-missing-version messages."""

from __future__ import annotations

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
    _warning_records,
    make_config,
    make_dependency_chain,
    make_workspace,
)


def test_run_rejects_allow_unpublished_with_live(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """Combining ``--live`` with the override flag is a hard error."""
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


def test_index_missing_name_extraction_failure_message_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the fatal message and warning for name extraction failures."""
    plan, _preparation, _staging_root = publish_plan_and_prep

    message = _handle_index_missing_version_message(
        plan,
        stderr=INDEX_MISSING_STDERR_UNPARSEABLE,
        allow_unpublished_workspace_deps=True,
        caplog=caplog,
    )

    assert message == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")


def test_index_missing_in_plan_downgrade_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the warning emitted when the flag downgrades a failure to a warning."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")
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

    assert message == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")


def test_index_missing_flag_disabled_message_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the fatal message and warning when the override is disabled."""
    plan, _preparation, _staging_root = publish_plan_and_prep

    message = _handle_index_missing_version_message(
        plan,
        stderr=INDEX_MISSING_STDERR_BETA,
        allow_unpublished_workspace_deps=False,
        caplog=caplog,
    )

    assert message == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")
