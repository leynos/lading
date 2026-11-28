"""Tests for prepared rewrite destinations and application metrics.

Preparation confines every planned destination beneath the workspace root
before proposing a write. The commit phase then records whether the applied
rewrites succeeded, so operators can see a failed write in the same metric
family that reports planning outcomes.
"""

import pathlib

import pytest

from lading.commands import bump, bump_manifests, bump_prepare_manifest_rewrites
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewritePlan,
)
from lading.commands.bump_plan_builder import PlanBuilder
from lading.config import BumpConfig, LadingConfig, ManifestRewriteConfig
from lading.workspace import WorkspaceGraph
from tests.helpers.manifest_rewrite_metrics import (
    manifest_rewrite_counter_value,
    manifest_rewrite_duration_stats,
)


def _context(
    root: pathlib.Path,
    configuration: LadingConfig,
) -> bump_manifests._BumpContext:
    """Build a minimal bump context for preparation-level tests."""
    return bump_manifests._BumpContext(
        root_path=root,
        configuration=configuration,
        workspace=WorkspaceGraph(workspace_root=root, crates=()),
        base_options=bump.BumpOptions(),
        workspace_manifest=root / "Cargo.toml",
        excluded=frozenset(),
        updated_crate_names=frozenset(),
    )


def _rewrite_configuration() -> LadingConfig:
    """Return a configuration that enables one rewrite group."""
    return LadingConfig(
        bump=BumpConfig(
            manifest_rewrites=(ManifestRewriteConfig(paths=("fixture/Cargo.toml",)),)
        )
    )


def test_plan_outside_the_workspace_root_is_rejected(tmp_path: pathlib.Path) -> None:
    """A destination that escapes the root is reported, not planned."""
    root = tmp_path / "workspace"
    root.mkdir()
    plan = ManifestRewritePlan(
        path=tmp_path / "elsewhere" / "Cargo.toml",
        original_text="",
        new_text="",
    )

    with pytest.raises(ManifestRewriteError, match="escaped the workspace root"):
        bump_prepare_manifest_rewrites._relative_rewrite_path(plan, root)


def test_escaping_rewrite_destination_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """A planned destination that resolves outside the root is not proposed."""
    root = tmp_path / "workspace"
    fixture = root / "fixture" / "Cargo.toml"
    fixture.parent.mkdir(parents=True)
    outside = tmp_path / "outside" / "Cargo.toml"
    outside.parent.mkdir()
    outside.write_text("[dependencies]\n", encoding="utf-8")
    fixture.symlink_to(outside)
    plan = ManifestRewritePlan(
        path=fixture,
        original_text="[dependencies]\n",
        new_text="[dependencies]\n",
    )

    with pytest.raises(ManifestRewriteError, match="cannot write the planned rewrite"):
        bump_prepare_manifest_rewrites._propose_rewrites(
            _context(root, _rewrite_configuration()),
            PlanBuilder(root=root),
            (plan,),
        )


@pytest.mark.usefixtures("reset_process_metrics")
def test_failed_application_metrics_record_the_write_category(
    tmp_path: pathlib.Path,
) -> None:
    """A failed commit records one application failure with its category."""
    context = _context(tmp_path, _rewrite_configuration())

    bump_prepare_manifest_rewrites.record_application_metrics(
        context, 0.25, failed=True
    )

    assert (
        manifest_rewrite_counter_value(
            phase="application",
            outcome="failed",
            dry_run="false",
            error_category="write",
        )
        == 1
    ), "a failed commit must be counted once under the write category"
    stats = manifest_rewrite_duration_stats(
        outcome="failed", dry_run="false", error_category="write"
    )
    assert stats.count == 1, "a failed commit must record one duration observation"


@pytest.mark.usefixtures("reset_process_metrics")
def test_application_metrics_skip_configurations_without_rewrites(
    tmp_path: pathlib.Path,
) -> None:
    """A configuration with no rewrite groups records no application metric."""
    context = _context(tmp_path, LadingConfig(bump=BumpConfig()))

    bump_prepare_manifest_rewrites.record_application_metrics(
        context, 0.25, failed=False
    )

    assert (
        manifest_rewrite_counter_value(
            phase="application",
            outcome="applied",
            dry_run="false",
            error_category="none",
        )
        == 0
    ), "an unconfigured run must not record an application outcome"
