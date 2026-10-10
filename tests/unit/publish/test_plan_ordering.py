"""Publish plan ordering behaviour tests."""

import typing as typ

from lading.commands import publish
from lading.workspace import WorkspaceDependency

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


def test_plan_publication_topologically_orders_dependencies(tmp_path: Path) -> None:
    """Crates are sorted so that dependencies publish before their dependents."""
    alpha, beta, gamma = make_dependency_chain(tmp_path.resolve())

    plan = plan_with_crates(tmp_path, (gamma, beta, alpha))

    assert plan.publishable == (alpha, beta, gamma), (
        "a crate must publish only after the dependencies it needs"
    )


def test_plan_publication_ignores_dev_dependency_cycles(tmp_path: Path) -> None:
    """Dev-only dependency edges do not create publish-order cycles."""
    root = tmp_path.resolve()
    alpha = make_crate(
        root,
        "alpha",
        dependencies=(
            WorkspaceDependency(
                package_id="beta-id",
                name="beta",
                manifest_name="beta",
                kind="dev",
            ),
        ),
    )
    beta = make_crate(root, "beta", dependencies=(make_dependency("alpha"),))
    workspace = make_workspace(root, alpha, beta)
    configuration = make_config()

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == (alpha, beta), (
        "a dev-only dependency edge must not exclude a crate from the plan"
    )


def test_plan_publication_ignores_cycles_in_non_publishable_crates(
    tmp_path: Path,
) -> None:
    """Cycles among skipped crates do not block eligible publishable crates."""
    root = tmp_path.resolve()
    alpha = make_crate(root, "alpha")
    cycle_a = make_crate(
        root,
        "cycle-a",
        publish_flag=False,
        dependencies=(make_dependency("cycle-b"),),
    )
    cycle_b = make_crate(
        root,
        "cycle-b",
        publish_flag=False,
        dependencies=(make_dependency("cycle-a"),),
    )

    plan = plan_with_crates(tmp_path, (alpha, cycle_a, cycle_b))

    assert plan.publishable == (alpha,), (
        "a cycle among crates that are not publishable must not block "
        "the publishable crates"
    )


def test_plan_publication_configuration_skips_ignore_cycles(tmp_path: Path) -> None:
    """Configuration exclusions bypass cycles outside publishable crates."""
    root = tmp_path.resolve()
    alpha = make_crate(root, "alpha")
    cycle_a = make_crate(root, "cycle-a", dependencies=(make_dependency("cycle-b"),))
    cycle_b = make_crate(root, "cycle-b", dependencies=(make_dependency("cycle-a"),))

    plan = plan_with_crates(
        tmp_path,
        (alpha, cycle_a, cycle_b),
        exclude=("cycle-a", "cycle-b"),
    )

    assert plan.publishable == (alpha,), (
        "crates excluded by configuration must not block or join the publishable set"
    )


def test_plan_publication_honours_configured_order(tmp_path: Path) -> None:
    """Explicit publish.order values override the automatic dependency sort."""
    alpha, beta, gamma = make_dependency_chain(tmp_path.resolve())

    plan = plan_with_crates(
        tmp_path,
        (alpha, beta, gamma),
        order=("gamma", "beta", "alpha"),
    )

    assert plan.publishable == (gamma, beta, alpha), (
        "publish.order must be honoured verbatim when it lists every crate"
    )
