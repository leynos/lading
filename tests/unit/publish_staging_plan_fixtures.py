"""Shared plan construction for the ``publish.stage_exclude`` tests.

The copy-filter tests and the manifest-protection tests both need the same
workspace: one publishable crate, one crate skipped by its manifest, and one
skipped by configuration. Building it in one place keeps each test module
focused on the behaviour it checks rather than on fixture assembly.
"""

from lading.commands import publish, publish_plan
from tests.unit.conftest import PreparationFixtures, _CrateSpec


def _plan_with_roles(fixtures: PreparationFixtures) -> publish_plan.PublishPlan:
    """Return a plan with a publishable, manifest-skipped, and config-skipped crate."""
    root = fixtures.tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    publishable = fixtures.make_crate(root, "alpha")
    manifest_skipped = fixtures.make_crate(root, "beta", _CrateSpec(publish=False))
    configuration_skipped = fixtures.make_crate(root, "gamma")
    workspace = fixtures.make_workspace(
        root, publishable, manifest_skipped, configuration_skipped
    )
    return publish.plan_publication(workspace, fixtures.make_config(exclude=("gamma",)))
