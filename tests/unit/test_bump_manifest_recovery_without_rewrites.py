"""Rollback member manifest writes when no fixture rewrites are configured."""

from pathlib import Path
from unittest import mock

import pytest

from lading import config as config_module
from lading.commands import bump, bump_manifests, bump_pipeline
from tests.helpers.workspace_builders import _make_workspace


class _InjectedMemberManifestError(Exception):
    """Failure injected after an earlier member manifest was updated."""


def test_member_failure_restores_writes_without_rewrite_configuration(
    tmp_path: Path,
) -> None:
    """An unconfigured stage still records outputs needed for rollback."""
    workspace = _make_workspace(tmp_path)
    manifest_paths = (
        tmp_path / "Cargo.toml",
        *(crate.manifest_path for crate in workspace.crates),
    )
    original_contents = {path: path.read_bytes() for path in manifest_paths}
    alpha_manifest = next(
        crate.manifest_path for crate in workspace.crates if crate.name == "alpha"
    )
    beta_manifest = next(
        crate.manifest_path for crate in workspace.crates if crate.name == "beta"
    )
    completed_updates: list[Path] = []
    original_update_manifest = bump_pipeline._update_manifest

    def fail_beta_update(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        options: bump.BumpOptions,
    ) -> bump_manifests._ManifestUpdateResult:
        """Fail beta after recording successful earlier updates."""
        if manifest_path == beta_manifest:
            raise _InjectedMemberManifestError
        result = original_update_manifest(
            manifest_path, selectors, target_version, options
        )
        if result.was_changed:
            completed_updates.append(manifest_path)
        return result

    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(rebuild_lockfiles=False)
    )
    with (
        mock.patch.object(
            bump_pipeline, "_update_manifest", side_effect=fail_beta_update
        ),
        pytest.raises(_InjectedMemberManifestError),
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

    assert alpha_manifest in completed_updates, (
        "the test must complete an earlier member update before failing beta"
    )
    assert {path: path.read_bytes() for path in manifest_paths} == original_contents, (
        "rollback should restore member writes without rewrite configuration"
    )
