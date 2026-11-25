"""Rollback member manifest writes when no fixture rewrites are configured."""

import typing as typ
from pathlib import Path
from unittest import mock

import pytest

from lading import config as config_module
from lading.commands import (
    bump,
    bump_manifests,
    bump_pipeline,
    bump_pipeline_crate_manifests,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteWriteError
from lading.exceptions import LadingError
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import _load_version, _make_workspace

if typ.TYPE_CHECKING:
    from lading.commands.bump_output import BumpChanges


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
        update_context: bump_manifests._ManifestUpdateContext,
    ) -> bump_manifests._ManifestUpdateResult:
        """Fail one member after recording successful earlier updates."""
        if manifest_path == failed_manifest:
            raise _InjectedMemberManifestError
        result = original_update_manifest(
            manifest_path, selectors, target_version, update_context
        )
        if result.was_changed:
            completed_updates.append(manifest_path)
        return result

    with (
        mock.patch.object(
            bump_pipeline, "_update_manifest", side_effect=fail_selected_update
        ),
        mock.patch.object(
            bump_pipeline_crate_manifests,
            "_update_manifest",
            side_effect=fail_selected_update,
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


def test_root_package_member_pass_preserves_intervening_edit(
    tmp_path: Path,
) -> None:
    """The member read rejects an edit made after its pre-update check."""
    workspace, root_manifest = _make_root_package_workspace(tmp_path)
    concurrent_edit = "\n# concurrent edit between bump passes\n"
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )
    original_update_manifest = bump_pipeline._update_manifest
    inserted_edit = False
    root_updates = 0

    def write_workspace_then_edit(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        update_context: bump_manifests._ManifestUpdateContext,
    ) -> bump_manifests._ManifestUpdateResult:
        """Insert an edit after member validation but before the root read."""
        nonlocal inserted_edit, root_updates
        if manifest_path == root_manifest:
            root_updates += 1
            if root_updates == 2:
                root_manifest.write_text(
                    root_manifest.read_text(encoding="utf-8") + concurrent_edit,
                    encoding="utf-8",
                )
                inserted_edit = True
        return original_update_manifest(
            manifest_path,
            selectors,
            target_version,
            update_context,
        )

    with (
        mock.patch.object(
            bump_pipeline,
            "_update_manifest",
            side_effect=write_workspace_then_edit,
        ),
        mock.patch.object(
            bump_pipeline_crate_manifests,
            "_update_manifest",
            side_effect=write_workspace_then_edit,
        ),
        pytest.raises(
            ManifestRewriteWriteError,
            match="contents changed after this bump wrote the manifest",
        ),
    ):
        bump.run(
            tmp_path,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=configuration,
                workspace=workspace,
            ),
        )

    current_text = root_manifest.read_text(encoding="utf-8")
    assert inserted_edit, "the concurrent edit must land between the two writes"
    assert current_text.endswith(concurrent_edit), (
        "rollback must preserve the intervening edit"
    )
    assert _load_version(root_manifest, ("workspace", "package")) == "1.2.3", (
        "rollback must preserve the workspace-pass version alongside the edit"
    )


def test_workspace_manifest_rejects_retargeted_root_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root manifest symlink cannot redirect the workspace update."""
    workspace = _make_workspace(tmp_path)
    manifest_entry = tmp_path / "Cargo.toml"
    original_destination = tmp_path / "workspace.toml"
    manifest_entry.rename(original_destination)
    manifest_entry.symlink_to(original_destination.name)
    alternate_destination = tmp_path / "alternate.toml"
    alternate_destination.write_bytes(original_destination.read_bytes())
    original_bytes = original_destination.read_bytes()
    alternate_bytes = alternate_destination.read_bytes()
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )
    original_run_pipeline = bump_pipeline._run_pipeline

    def retarget_before_pipeline(
        context: bump_manifests._BumpContext,
        target_version: str,
    ) -> BumpChanges:
        """Retarget the root manifest after its destination was recorded."""
        manifest_entry.unlink()
        manifest_entry.symlink_to(alternate_destination.name)
        return original_run_pipeline(context, target_version)

    monkeypatch.setattr(bump_pipeline, "_run_pipeline", retarget_before_pipeline)
    with pytest.raises(LadingError, match="destination changed after bump context"):
        bump.run(
            tmp_path,
            "1.2.3",
            options=bump.BumpOptions(
                configuration=configuration,
                workspace=workspace,
            ),
        )

    assert original_destination.read_bytes() == original_bytes, (
        "the recorded root target must remain unchanged after symlink retargeting"
    )
    assert alternate_destination.read_bytes() == alternate_bytes, (
        "the alternate target must not receive a workspace manifest update"
    )
