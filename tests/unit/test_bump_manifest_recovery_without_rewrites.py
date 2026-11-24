"""Rollback member manifest writes when no fixture rewrites are configured."""

from pathlib import Path
from unittest import mock

import pytest

from lading import config as config_module
from lading.commands import bump, bump_manifests, bump_pipeline
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import _make_workspace


class _InjectedMemberManifestError(Exception):
    """Failure injected after an earlier member manifest was updated."""


def _manifest_paths_by_name(workspace: WorkspaceGraph) -> dict[str, Path]:
    """Return each member's manifest path by package name."""
    return {crate.name: crate.manifest_path for crate in workspace.crates}


def _run_bump_with_member_failure(
    root: Path,
    workspace: WorkspaceGraph,
    configuration: config_module.LadingConfig,
    failed_manifest: Path,
) -> tuple[Path, ...]:
    """Fail a selected member update and return paths written before it."""
    completed_updates: list[Path] = []
    original_update_manifest = bump_pipeline._update_manifest

    def fail_selected_update(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        options: bump.BumpOptions,
    ) -> bump_manifests._ManifestUpdateResult:
        """Fail one member after recording successful earlier updates."""
        if manifest_path == failed_manifest:
            raise _InjectedMemberManifestError
        result = original_update_manifest(
            manifest_path, selectors, target_version, options
        )
        if result.was_changed:
            completed_updates.append(manifest_path)
        return result

    with (
        mock.patch.object(
            bump_pipeline, "_update_manifest", side_effect=fail_selected_update
        ),
        pytest.raises(_InjectedMemberManifestError),
    ):
        bump.run(
            root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=configuration,
                workspace=workspace,
            ),
        )
    return tuple(completed_updates)


def _make_root_package_workspace(root: Path) -> tuple[WorkspaceGraph, Path]:
    """Add a root package with an aliased member dependency."""
    workspace = _make_workspace(root)
    root_manifest = root / "Cargo.toml"
    with root_manifest.open("a", encoding="utf-8") as handle:
        handle.write(
            '\n[package]\nname = "root-package"\nversion = "0.1.0"\n'
            '\n[dependencies]\nalpha-renamed = { package = "alpha", '
            'version = "0.1.0" }\n'
        )
    root_crate = WorkspaceCrate(
        name="root-package",
        version="0.1.0",
        manifest_path=root_manifest,
        root_path=root,
        publish=True,
        readme_is_workspace=False,
        dependencies=(
            WorkspaceDependency(
                package_id="alpha 0.1.0",
                name="alpha",
                manifest_name="alpha-renamed",
            ),
        ),
    )
    graph = WorkspaceGraph(
        workspace_root=root,
        crates=(root_crate, *workspace.crates),
    )
    return graph, root_manifest


def test_member_failure_restores_writes_without_rewrite_configuration(
    tmp_path: Path,
) -> None:
    """An unconfigured stage still records outputs needed for rollback."""
    workspace = _make_workspace(tmp_path)
    member_manifests = _manifest_paths_by_name(workspace)
    manifest_paths = (tmp_path / "Cargo.toml", *member_manifests.values())
    original_contents = {path: path.read_bytes() for path in manifest_paths}
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )

    completed_updates = _run_bump_with_member_failure(
        tmp_path,
        workspace,
        configuration,
        member_manifests["beta"],
    )

    assert member_manifests["alpha"] in completed_updates, (
        "the test must complete an earlier member update before failing beta"
    )
    assert {path: path.read_bytes() for path in manifest_paths} == original_contents, (
        "rollback should restore member writes without rewrite configuration"
    )


def test_root_package_rollback_keeps_first_manifest_baseline(
    tmp_path: Path,
) -> None:
    """The package pass cannot replace the earlier workspace baseline."""
    workspace, root_manifest = _make_root_package_workspace(tmp_path)
    member_manifests = _manifest_paths_by_name(workspace)
    manifest_paths = (root_manifest, *member_manifests.values())
    original_contents = {path: path.read_bytes() for path in manifest_paths}
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )
    completed_updates = _run_bump_with_member_failure(
        tmp_path,
        workspace,
        configuration,
        member_manifests["beta"],
    )

    assert completed_updates.count(root_manifest) == 2, (
        "workspace and package passes must both update the same root manifest"
    )
    assert {path: path.read_bytes() for path in manifest_paths} == original_contents, (
        "rollback must restore the first text captured for the shared manifest"
    )
