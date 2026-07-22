"""Publish plan validation error handling tests."""

from __future__ import annotations

import typing as typ

import pytest

from lading.commands import publish, publish_plan

from .conftest import (
    make_config,
    make_crate,
    make_dependency,
    make_dependency_chain,
    make_workspace,
    plan_with_crates,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_plan_publication_rejects_incomplete_configured_order(tmp_path: Path) -> None:
    """Missing crates in publish.order surface a descriptive validation error."""
    root = tmp_path.resolve()
    alpha = make_crate(root, "alpha")
    beta = make_crate(root, "beta")
    workspace = make_workspace(root, alpha, beta)
    configuration = make_config(order=("alpha",))

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        publish.plan_publication(workspace, configuration)

    message = str(excinfo.value)
    assert "publish.order omits" in message
    assert "beta" in message


def test_plan_publication_rejects_unknown_configured_crates(tmp_path: Path) -> None:
    """Names outside the publishable set trigger an informative error."""
    alpha, _, _ = make_dependency_chain(tmp_path.resolve())

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        plan_with_crates(tmp_path, (alpha,), order=("alpha", "omega"))

    assert "publish.order references crates outside the publishable set" in str(
        excinfo.value
    )


def test_plan_publication_detects_dependency_cycles(tmp_path: Path) -> None:
    """A dependency cycle raises an explicit planning error."""
    root = tmp_path.resolve()
    alpha = make_crate(root, "alpha", dependencies=(make_dependency("beta"),))
    beta = make_crate(root, "beta", dependencies=(make_dependency("alpha"),))
    workspace = make_workspace(root, alpha, beta)
    configuration = make_config()

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        publish.plan_publication(workspace, configuration)

    assert "dependency cycle" in str(excinfo.value)
