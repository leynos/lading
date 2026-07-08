"""Unit tests covering publish plan derivation."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

import pytest

from lading.commands import publish, publish_plan
from tests.unit.conftest import PlanningFixtures, _CrateSpec

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading import config as config_module
    from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph


def _plan_with_crates(
    tmp_path: Path,
    make_workspace: cabc.Callable[[Path, WorkspaceCrate], WorkspaceGraph],
    make_config: cabc.Callable[..., config_module.LadingConfig],
    crates: tuple[WorkspaceCrate, ...],
    **config_overrides: object,
) -> publish.PublishPlan:
    """Plan publication for ``crates`` using ``tmp_path`` as the workspace root."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, *crates)
    configuration = make_config(**config_overrides)
    return publish.plan_publication(workspace, configuration)


def _make_dependency_chain(
    root: Path,
    *,
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate],
    make_dependency: cabc.Callable[[str], WorkspaceDependency],
) -> tuple[WorkspaceCrate, WorkspaceCrate, WorkspaceCrate]:
    """Return crates that form a simple alpha→beta→gamma dependency chain."""
    alpha = make_crate(root, "alpha")
    beta = make_crate(
        root,
        "beta",
        _CrateSpec(dependencies=(make_dependency("alpha"),)),
    )
    gamma = make_crate(
        root,
        "gamma",
        _CrateSpec(dependencies=(make_dependency("beta"),)),
    )
    return alpha, beta, gamma


def _create_cycle(
    fixtures: PlanningFixtures,
    *,
    name_a: str = "cycle-a",
    name_b: str = "cycle-b",
    publish_a: bool = True,
    publish_b: bool = True,
) -> tuple[WorkspaceCrate, WorkspaceCrate]:
    """Return two crates with mutual dependencies forming a cycle."""
    root = fixtures.tmp_path.resolve()
    crate_a = fixtures.make_crate(
        root,
        name_a,
        _CrateSpec(
            publish=publish_a,
            dependencies=(fixtures.make_dependency(name_b),),
        ),
    )
    crate_b = fixtures.make_crate(
        root,
        name_b,
        _CrateSpec(
            publish=publish_b,
            dependencies=(fixtures.make_dependency(name_a),),
        ),
    )
    return crate_a, crate_b


@pytest.mark.parametrize(
    (
        "crate_specs",
        "exclude",
        "expected",
    ),
    [
        pytest.param(
            [("alpha", True), ("beta", False), ("gamma", True)],
            ["gamma"],
            {
                "publishable": ("alpha",),
                "manifest": ("beta",),
                "configuration": ("gamma",),
            },
            id="filters_manifest_and_configuration",
        ),
        pytest.param(
            [("alpha", False), ("beta", False)],
            [],
            {
                "publishable": (),
                "manifest": ("alpha", "beta"),
                "configuration": (),
            },
            id="handles_no_publishable_crates",
        ),
    ],
)
def test_plan_publication_filtering(
    planning_fixtures: PlanningFixtures,
    crate_specs: list[tuple[str, bool]],
    exclude: list[str],
    expected: dict[str, tuple[str, ...]],
) -> None:
    """Planner splits crates into publishable and skipped groups."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    crates = [
        fx.make_crate(root, name, _CrateSpec(publish=publish_flag))
        for name, publish_flag in crate_specs
    ]
    workspace = fx.make_workspace(root, *crates)
    configuration = fx.make_config(exclude=tuple(exclude))

    plan = publish.plan_publication(workspace, configuration)

    actual_publishable_names = tuple(crate.name for crate in plan.publishable)
    actual_manifest_names = tuple(crate.name for crate in plan.skipped_manifest)
    actual_configuration_names = tuple(
        crate.name for crate in plan.skipped_configuration
    )

    assert actual_publishable_names == expected["publishable"]
    assert actual_manifest_names == expected["manifest"]
    assert actual_configuration_names == expected["configuration"]


def test_plan_publication_empty_workspace(
    tmp_path: Path,
    make_config: cabc.Callable[..., config_module.LadingConfig],
) -> None:
    """Planner returns empty results when the workspace has no crates."""
    from lading.workspace import WorkspaceGraph

    root = tmp_path.resolve()
    workspace = WorkspaceGraph(workspace_root=root, crates=())
    configuration = make_config()

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == ()
    assert plan.skipped_manifest == ()
    assert plan.skipped_configuration == ()


def test_plan_publication_empty_exclude_list(
    planning_fixtures: PlanningFixtures,
) -> None:
    """Configuration exclusions default to publishing all eligible crates."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    publishable = fx.make_crate(root, "alpha")
    manifest_skipped = fx.make_crate(root, "beta", _CrateSpec(publish=False))
    workspace = fx.make_workspace(root, publishable, manifest_skipped)
    configuration = fx.make_config(exclude=())

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == (publishable,)
    assert plan.skipped_manifest == (manifest_skipped,)
    assert plan.skipped_configuration == ()


@pytest.mark.parametrize(
    ("exclusions", "expected"),
    [
        pytest.param(("missing",), ("missing",), id="single"),
        pytest.param(
            ("missing1", "missing2", "missing3"),
            ("missing1", "missing2", "missing3"),
            id="multiple_ordered",
        ),
    ],
)
def test_plan_publication_records_missing_exclusions(
    planning_fixtures: PlanningFixtures,
    exclusions: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    """Unknown entries in publish.exclude are reported in the plan."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    workspace = fx.make_workspace(root)
    configuration = fx.make_config(exclude=exclusions)

    plan = publish.plan_publication(workspace, configuration)

    assert plan.missing_configuration_exclusions == expected


def test_plan_publication_sorts_crates_by_name(
    planning_fixtures: PlanningFixtures,
) -> None:
    """Publishable and skipped crates appear in deterministic alphabetical order."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    publishable_second = fx.make_crate(root, "beta")
    publishable_first = fx.make_crate(root, "alpha")
    manifest_skipped_late = fx.make_crate(root, "epsilon", _CrateSpec(publish=False))
    manifest_skipped_early = fx.make_crate(root, "delta", _CrateSpec(publish=False))
    config_skipped_late = fx.make_crate(root, "theta")
    config_skipped_early = fx.make_crate(root, "gamma")
    workspace = fx.make_workspace(
        root,
        publishable_second,
        publishable_first,
        manifest_skipped_late,
        manifest_skipped_early,
        config_skipped_late,
        config_skipped_early,
    )
    configuration = fx.make_config(exclude=("gamma", "theta"))

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == (publishable_first, publishable_second)
    assert plan.skipped_manifest == (manifest_skipped_early, manifest_skipped_late)
    assert plan.skipped_configuration == (config_skipped_early, config_skipped_late)


def test_plan_publication_multiple_configuration_skips(
    planning_fixtures: PlanningFixtures,
) -> None:
    """All configuration exclusions appear in the skipped configuration list."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    gamma = fx.make_crate(root, "gamma")
    delta = fx.make_crate(root, "delta")
    workspace = fx.make_workspace(root, gamma, delta)
    configuration = fx.make_config(exclude=("delta", "gamma"))

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == ()
    assert plan.skipped_configuration == (delta, gamma)


def test_plan_publication_topologically_orders_dependencies(
    planning_fixtures: PlanningFixtures,
) -> None:
    """Crates are sorted so that dependencies publish before their dependents."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    alpha, beta, gamma = _make_dependency_chain(
        root, make_crate=fx.make_crate, make_dependency=fx.make_dependency
    )

    plan = _plan_with_crates(
        fx.tmp_path,
        fx.make_workspace,
        fx.make_config,
        (gamma, beta, alpha),
    )

    assert plan.publishable == (alpha, beta, gamma)


def test_plan_publication_ignores_dev_dependency_cycles(
    planning_fixtures: PlanningFixtures,
) -> None:
    """Dev-only dependency edges do not create publish-order cycles."""
    from lading.workspace import WorkspaceDependency

    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    alpha = fx.make_crate(
        root,
        "alpha",
        _CrateSpec(
            dependencies=(
                WorkspaceDependency(
                    package_id="beta-id",
                    name="beta",
                    manifest_name="beta",
                    kind="dev",
                ),
            )
        ),
    )
    beta = fx.make_crate(
        root,
        "beta",
        _CrateSpec(dependencies=(fx.make_dependency("alpha"),)),
    )
    workspace = fx.make_workspace(root, alpha, beta)
    configuration = fx.make_config()

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == (alpha, beta)


def test_plan_publication_detects_dependency_cycles(
    planning_fixtures: PlanningFixtures,
) -> None:
    """A dependency cycle raises an explicit planning error."""
    alpha, beta = _create_cycle(
        planning_fixtures,
        name_a="alpha",
        name_b="beta",
    )

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        _plan_with_crates(
            planning_fixtures.tmp_path,
            planning_fixtures.make_workspace,
            planning_fixtures.make_config,
            (alpha, beta),
        )

    assert "dependency cycle" in str(excinfo.value)


@pytest.mark.parametrize(
    ("cycle_publish_flags", "excludes", "scenario"),
    [
        pytest.param(
            {"publish_a": False, "publish_b": False},
            (),
            "manifest",
            id="ignores_cycles_in_non_publishable_crates",
        ),
        pytest.param(
            {},
            ("cycle-a", "cycle-b"),
            "configuration",
            id="configuration_skips_ignore_cycles",
        ),
    ],
)
def test_plan_publication_ignores_cycles_in_skipped_crates(
    planning_fixtures: PlanningFixtures,
    cycle_publish_flags: dict[str, bool],
    excludes: tuple[str, ...],
    scenario: str,
) -> None:
    """Cycles skipped via manifest or configuration do not block publishable crates."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    alpha = fx.make_crate(root, "alpha")
    cycle_a, cycle_b = _create_cycle(fx, **cycle_publish_flags)

    plan = _plan_with_crates(
        fx.tmp_path,
        fx.make_workspace,
        fx.make_config,
        (alpha, cycle_a, cycle_b),
        exclude=excludes,
    )

    assert plan.publishable == (alpha,)


def test_plan_publication_honours_configured_order(
    planning_fixtures: PlanningFixtures,
) -> None:
    """Explicit publish.order values override the automatic dependency sort."""
    fx = planning_fixtures
    alpha, beta, gamma = _make_dependency_chain(
        fx.tmp_path.resolve(),
        make_crate=fx.make_crate,
        make_dependency=fx.make_dependency,
    )

    plan = _plan_with_crates(
        fx.tmp_path,
        fx.make_workspace,
        fx.make_config,
        (alpha, beta, gamma),
        order=("gamma", "beta", "alpha"),
    )

    assert plan.publishable == (gamma, beta, alpha)


def test_plan_publication_rejects_incomplete_configured_order(
    planning_fixtures: PlanningFixtures,
) -> None:
    """Missing crates in publish.order surface a descriptive validation error."""
    fx = planning_fixtures
    root = fx.tmp_path.resolve()
    alpha = fx.make_crate(root, "alpha")
    beta = fx.make_crate(root, "beta")
    workspace = fx.make_workspace(root, alpha, beta)
    configuration = fx.make_config(order=("alpha",))

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        publish.plan_publication(workspace, configuration)

    message = str(excinfo.value)
    assert "publish.order omits" in message
    assert "beta" in message


@pytest.mark.parametrize(
    ("order", "expected_error"),
    [
        pytest.param(
            ("alpha", "alpha"),
            "Duplicate publish.order entries: alpha",
            id="rejects_duplicate",
        ),
        pytest.param(
            ("alpha", "omega"),
            "publish.order references crates outside the publishable set",
            id="rejects_unknown",
        ),
    ],
)
def test_plan_publication_order_validation_errors(
    planning_fixtures: PlanningFixtures,
    order: tuple[str, ...],
    expected_error: str,
) -> None:
    """Invalid publish.order configurations trigger informative errors."""
    fx = planning_fixtures
    alpha, _, _ = _make_dependency_chain(
        fx.tmp_path.resolve(),
        make_crate=fx.make_crate,
        make_dependency=fx.make_dependency,
    )

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        _plan_with_crates(
            fx.tmp_path,
            fx.make_workspace,
            fx.make_config,
            (alpha,),
            order=order,
        )

    assert expected_error in str(excinfo.value)
