"""Tests for the publish index-lookup downgrade counter (issue #68)."""

from __future__ import annotations

import collections.abc as cabc
import json
import logging
import typing as typ

import pytest

from lading.commands import publish, publish_index_check
from lading.commands.cargo_output_adapter import CargoIndexLookupFailure
from lading.utils import metrics

from .conftest import (
    INDEX_MISSING_STDERR_BETA,
    INDEX_MISSING_STDERR_EXTERNAL,
    make_config,
    make_dependency_chain,
    make_workspace,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_METRIC = publish_index_check.INDEX_LOOKUP_DOWNGRADE_METRIC


@pytest.fixture(autouse=True)
def _reset_metrics() -> cabc.Iterator[None]:
    """Isolate the metric registry for each test."""
    metrics.reset()
    yield
    metrics.reset()


def _invoke_handler(
    tmp_path: Path,
    *,
    stderr: str,
    missing_crate: str,
    allow_unpublished_workspace_deps: bool,
) -> None:
    """Drive ``_handle_index_missing_version`` for crate beta."""
    workspace_root = tmp_path / "workspace"
    crates = make_dependency_chain(workspace_root)
    plan = publish.plan_publication(
        make_workspace(workspace_root, *crates), make_config()
    )
    failure = CargoIndexLookupFailure(
        crate_name="beta",
        subcommand="package",
        exit_code=1,
        stdout="",
        stderr=stderr,
        missing_dependency_name=missing_crate,
    )
    publish._handle_index_missing_version(
        failure,
        plan=plan,
        options=publish._PublishExecutionOptions(
            live=False,
            allow_dirty=True,
            allow_unpublished_workspace_deps=allow_unpublished_workspace_deps,
        ),
    )


def test_downgrade_path_increments_counter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The in-plan downgrade increments the labelled counter once."""
    caplog.set_level(logging.WARNING, logger="lading.commands.publish")

    _invoke_handler(
        tmp_path,
        stderr=INDEX_MISSING_STDERR_BETA,
        missing_crate="alpha",
        allow_unpublished_workspace_deps=True,
    )

    assert (
        metrics.counter_value(_METRIC, subcommand="package", missing_crate="alpha") == 1
    )


@pytest.mark.parametrize(
    ("stderr", "missing_crate", "allow_unpublished_workspace_deps"),
    [
        pytest.param(INDEX_MISSING_STDERR_BETA, "alpha", False, id="flag_disabled"),
        pytest.param(
            INDEX_MISSING_STDERR_EXTERNAL,
            "external_crate",
            True,
            id="out_of_plan",
        ),
    ],
)
def test_raise_paths_do_not_increment_counter(
    tmp_path: Path,
    *,
    stderr: str,
    missing_crate: str,
    allow_unpublished_workspace_deps: bool,
) -> None:
    """Neither the flag-disabled nor the out-of-plan path counts a downgrade."""
    with pytest.raises(publish.PublishPreflightError):
        _invoke_handler(
            tmp_path,
            stderr=stderr,
            missing_crate=missing_crate,
            allow_unpublished_workspace_deps=allow_unpublished_workspace_deps,
        )

    assert metrics.snapshot() == {}


def _make_beta_package_index_failure_runner() -> cabc.Callable[
    [cabc.Sequence[str]], tuple[int, str, str]
]:
    """Return a runner whose ``cargo package`` for crate beta misses the index."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del env
        is_package = tuple(command[:2]) == ("cargo", "package")
        is_beta = cwd is not None and cwd.name == "beta"
        if is_package and is_beta:
            return (1, "", INDEX_MISSING_STDERR_BETA)
        return (0, "", "")

    return runner


def test_full_publish_run_records_and_emits_downgrade_metric(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An end-to-end downgrade is counted and surfaced in the exit summary.

    Drives ``publish.run`` through the real index-failure parsing and downgrade
    path (rather than the handler in isolation), then confirms ``emit_summary``
    renders the counter operators see at process exit.
    """
    caplog.set_level(logging.INFO, logger="lading.utils.metrics")
    root = tmp_path / "workspace"
    workspace = make_workspace(root, *make_dependency_chain(root))
    configuration = make_config()

    publish.run(
        root,
        configuration,
        workspace,
        options=publish.PublishOptions(
            build_directory=tmp_path / "build",
            command_runner=_make_beta_package_index_failure_runner(),
            allow_unpublished_workspace_deps=True,
        ),
    )

    assert (
        metrics.counter_value(_METRIC, subcommand="package", missing_crate="alpha") == 1
    )

    metrics.emit_summary()

    summaries = [
        record.getMessage()
        for record in caplog.records
        if "metrics summary" in record.getMessage()
    ]
    assert len(summaries) == 1
    payload = json.loads(summaries[0].partition(": ")[2])
    assert {
        "metric": _METRIC,
        "labels": {"missing_crate": "alpha", "subcommand": "package"},
        "value": 1,
    } in payload
