"""Tests for the publish index-lookup downgrade counter (issue #68)."""

from __future__ import annotations

import collections.abc as cabc
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
    assert metrics.snapshot() == {
        (_METRIC, (("missing_crate", "alpha"), ("subcommand", "package"))): 1
    }


def test_raise_paths_do_not_increment_counter(tmp_path: Path) -> None:
    """Neither the flag-disabled nor the out-of-plan path counts a downgrade."""
    with pytest.raises(publish.PublishPreflightError):
        _invoke_handler(
            tmp_path,
            stderr=INDEX_MISSING_STDERR_BETA,
            missing_crate="alpha",
            allow_unpublished_workspace_deps=False,
        )
    with pytest.raises(publish.PublishPreflightError):
        _invoke_handler(
            tmp_path,
            stderr=INDEX_MISSING_STDERR_EXTERNAL,
            missing_crate="external_crate",
            allow_unpublished_workspace_deps=True,
        )

    assert metrics.snapshot() == {}
