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

import logging
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

import pytest

from lading.commands import publish
from lading.commands.cargo_output_adapter import (
    CargoIndexLookupFailure,
    parse_index_lookup_failure,
)

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


def _missing_dependency_name(stderr: str) -> str | None:
    """Return the missing dependency parsed by the cargo output adapter."""
    failure = parse_index_lookup_failure(
        crate_name="beta",
        subcommand="package",
        exit_code=1,
        stdout="",
        stderr=stderr,
    )
    if failure is None:
        return None
    return failure.missing_dependency_name


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
            "--allow-unpublished-workspace-deps is only valid in dry-run mode; "
            "re-run without --live."
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
    failure = CargoIndexLookupFailure(
        crate_name="beta",
        subcommand="package",
        exit_code=1,
        stdout="",
        stderr=stderr,
        missing_dependency_name=_missing_dependency_name(stderr),
    )

    with pytest.raises(publish.PublishPreflightError) as excinfo:
        publish._handle_index_missing_version(
            failure,
            plan=plan,
            options=publish._PublishExecutionOptions(
                live=False,
                allow_dirty=True,
                allow_unpublished_workspace_deps=allow_unpublished_workspace_deps,
            ),
        )

    return str(excinfo.value)


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

    assert message == snapshot(name="message")
    assert _warning_records(caplog) == snapshot(name="warning")


def test_index_missing_in_plan_downgrade_snapshot(
    publish_plan_and_prep: tuple[publish.PublishPlan, publish.PublishPreparation, Path],
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the warning emitted when the flag downgrades a failure to a warning."""
    caplog.set_level(logging.INFO)
    plan, _preparation, _staging_root = publish_plan_and_prep
    failure = CargoIndexLookupFailure(
        crate_name="beta",
        subcommand="package",
        exit_code=1,
        stdout="",
        stderr=INDEX_MISSING_STDERR_BETA,
        missing_dependency_name="alpha",
    )

    # Must not raise - the success/downgrade path returns without raising.
    publish._handle_index_missing_version(
        failure,
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
        and "dependency alpha is part of the publish plan" in message
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

    assert message == snapshot(name="message")
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
