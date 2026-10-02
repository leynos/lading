"""Unit tests covering publish plan derivation."""

import collections.abc as cabc
import typing as typ

import pytest

from lading.commands import publish, publish_plan
from tests.unit.conftest import (
    PublishFixtures,
    _CrateFactory,
    _CrateSpec,
    _WorkspaceFactory,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading import config as config_module
    from lading.workspace import WorkspaceCrate, WorkspaceDependency


def _plan_with_crates(
    tmp_path: Path,
    make_workspace: _WorkspaceFactory,
    make_config: cabc.Callable[..., config_module.LadingConfig],
    crates: tuple[WorkspaceCrate, ...],
    **config_overrides: object,
) -> publish_plan.PublishPlan:
    """Plan publication for ``crates`` using ``tmp_path`` as the workspace root."""
    root = tmp_path.resolve()
    workspace = make_workspace(root, *crates)
    configuration = make_config(**config_overrides)
    return publish.plan_publication(workspace, configuration)


def _make_dependency_chain(
    root: Path,
    *,
    make_crate: _CrateFactory,
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


class _CyclePublishFlags(typ.TypedDict, total=False):
    """Which of a cycle's two crates should be publishable.

    Typed rather than ``dict[str, bool]``: an unpacked bare mapping cannot be
    checked against ``_create_cycle``'s other string keywords, so ty rejects
    the call even though only these two keys are ever supplied.
    """

    publish_a: bool
    publish_b: bool


def _create_cycle(
    fixtures: PublishFixtures,
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
    publish_fixtures: PublishFixtures,
    crate_specs: list[tuple[str, bool]],
    exclude: list[str],
    expected: dict[str, tuple[str, ...]],
) -> None:
    """Planner splits crates into publishable and skipped groups."""
    fx = publish_fixtures
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

    assert actual_publishable_names == expected["publishable"], (
        "crates with publish enabled and no exclusion must be publishable"
    )
    assert actual_manifest_names == expected["manifest"], (
        "crates whose manifest sets publish = false must be manifest-skipped"
    )
    assert actual_configuration_names == expected["configuration"], (
        "crates named in publish.exclude must be configuration-skipped"
    )


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

    assert not plan.publishable, (
        "an empty workspace must leave the publishable group empty"
    )
    assert not plan.skipped_manifest, (
        "an empty workspace must leave the manifest-skipped group empty"
    )
    assert not plan.skipped_configuration, (
        "an empty workspace must leave the configuration-skipped group empty"
    )


def test_plan_publication_empty_exclude_list(
    publish_fixtures: PublishFixtures,
) -> None:
    """Configuration exclusions default to publishing all eligible crates."""
    fx = publish_fixtures
    root = fx.tmp_path.resolve()
    publishable = fx.make_crate(root, "alpha")
    manifest_skipped = fx.make_crate(root, "beta", _CrateSpec(publish=False))
    workspace = fx.make_workspace(root, publishable, manifest_skipped)
    configuration = fx.make_config(exclude=())

    plan = publish.plan_publication(workspace, configuration)

    assert plan.publishable == (publishable,), (
        "an empty exclusion list must leave every eligible crate publishable"
    )
    assert plan.skipped_manifest == (manifest_skipped,), (
        "a crate with publish = false must stay manifest-skipped when no "
        "configuration exclusions are set"
    )
    assert not plan.skipped_configuration, (
        "no crate may be configuration-skipped when publish.exclude is empty"
    )


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
    publish_fixtures: PublishFixtures,
    exclusions: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    """Unknown entries in publish.exclude are reported in the plan."""
    fx = publish_fixtures
    root = fx.tmp_path.resolve()
    workspace = fx.make_workspace(root)
    configuration = fx.make_config(exclude=exclusions)

    plan = publish.plan_publication(workspace, configuration)

    assert plan.missing_configuration_exclusions == expected, (
        "unknown publish.exclude entries must be reported in the configured order"
    )


def test_plan_publication_sorts_crates_by_name(
    publish_fixtures: PublishFixtures,
) -> None:
    """Publishable and skipped crates appear in deterministic alphabetical order."""
    fx = publish_fixtures
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

    assert plan.publishable == (publishable_first, publishable_second), (
        "publishable crates must be sorted alphabetically by name"
    )
    assert plan.skipped_manifest == (manifest_skipped_early, manifest_skipped_late), (
        "manifest-skipped crates must be sorted alphabetically by name"
    )
    assert plan.skipped_configuration == (config_skipped_early, config_skipped_late), (
        "configuration-skipped crates must be sorted alphabetically by name"
    )


def test_plan_publication_multiple_configuration_skips(
    publish_fixtures: PublishFixtures,
) -> None:
    """All configuration exclusions appear in the skipped configuration list."""
    fx = publish_fixtures
    root = fx.tmp_path.resolve()
    gamma = fx.make_crate(root, "gamma")
    delta = fx.make_crate(root, "delta")
    workspace = fx.make_workspace(root, gamma, delta)
    configuration = fx.make_config(exclude=("delta", "gamma"))

    plan = publish.plan_publication(workspace, configuration)

    assert not plan.publishable, (
        "excluding every workspace crate must leave nothing publishable"
    )
    assert plan.skipped_configuration == (delta, gamma), (
        "all publish.exclude matches must appear in the configuration-skipped "
        "group in sorted order"
    )


def test_plan_publication_topologically_orders_dependencies(
    publish_fixtures: PublishFixtures,
) -> None:
    """Crates are sorted so that dependencies publish before their dependents."""
    fx = publish_fixtures
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

    assert plan.publishable == (alpha, beta, gamma), (
        "each crate must precede its dependent in the automatic publish order"
    )


def test_plan_publication_ignores_dev_dependency_cycles(
    publish_fixtures: PublishFixtures,
) -> None:
    """Dev-only dependency edges do not create publish-order cycles."""
    from lading.workspace import WorkspaceDependency

    fx = publish_fixtures
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

    assert plan.publishable == (alpha, beta), (
        "a dev-only dependency edge must not change the publish order"
    )


def test_plan_publication_detects_dependency_cycles(
    publish_fixtures: PublishFixtures,
) -> None:
    """A dependency cycle raises an explicit planning error."""
    alpha, beta = _create_cycle(
        publish_fixtures,
        name_a="alpha",
        name_b="beta",
    )

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        _plan_with_crates(
            publish_fixtures.tmp_path,
            publish_fixtures.make_workspace,
            publish_fixtures.make_config,
            (alpha, beta),
        )

    assert "dependency cycle" in str(excinfo.value), (
        "a mutual dependency must be reported as a dependency cycle"
    )


def test_publish_reexports_plan_error_for_public_callers(
    publish_fixtures: PublishFixtures,
) -> None:
    """``publish.plan_publication`` failures are catchable via ``publish``.

    ``plan_publication`` is public on the ``publish`` module, so the exception
    it raises must remain catchable as ``publish.PublishPlanError``. This guards
    the public re-export against removal alongside private compatibility shims.
    """
    assert publish.PublishPlanError is publish_plan.PublishPlanError, (
        "publish must re-export the canonical PublishPlanError so callers can "
        "catch planning failures via publish.PublishPlanError"
    )

    alpha, beta = _create_cycle(
        publish_fixtures,
        name_a="alpha",
        name_b="beta",
    )

    with pytest.raises(publish.PublishPlanError, match="dependency cycle"):
        _plan_with_crates(
            publish_fixtures.tmp_path,
            publish_fixtures.make_workspace,
            publish_fixtures.make_config,
            (alpha, beta),
        )


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
    publish_fixtures: PublishFixtures,
    cycle_publish_flags: _CyclePublishFlags,
    excludes: tuple[str, ...],
    scenario: str,
) -> None:
    """Cycles skipped via manifest or configuration do not block publishable crates."""
    fx = publish_fixtures
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

    assert plan.publishable == (alpha,), (
        "a dependency cycle confined to skipped crates must not block alpha"
    )


def test_plan_publication_honours_configured_order(
    publish_fixtures: PublishFixtures,
) -> None:
    """Explicit publish.order values override the automatic dependency sort."""
    fx = publish_fixtures
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

    assert plan.publishable == (gamma, beta, alpha), (
        "publish.order must override the automatic dependency sort"
    )


def test_plan_publication_rejects_incomplete_configured_order(
    publish_fixtures: PublishFixtures,
) -> None:
    """Missing crates in publish.order surface a descriptive validation error."""
    fx = publish_fixtures
    root = fx.tmp_path.resolve()
    alpha = fx.make_crate(root, "alpha")
    beta = fx.make_crate(root, "beta")
    workspace = fx.make_workspace(root, alpha, beta)
    configuration = fx.make_config(order=("alpha",))

    with pytest.raises(publish_plan.PublishPlanError) as excinfo:
        publish.plan_publication(workspace, configuration)

    message = str(excinfo.value)
    assert "publish.order omits" in message, (
        "an incomplete publish.order must be reported as omitting crates"
    )
    assert "beta" in message, (
        "the omitted crate name must be named in the publish.order error"
    )


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
    publish_fixtures: PublishFixtures,
    order: tuple[str, ...],
    expected_error: str,
) -> None:
    """Invalid publish.order configurations trigger informative errors."""
    fx = publish_fixtures
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

    assert expected_error in str(excinfo.value), (
        "the publish.order validation error must explain the offending entry"
    )
