"""Workspace-bound rollback for configured manifest rewrites."""

from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_pipeline_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteWriteError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case


def test_rollback_does_not_follow_manifest_symlink_outside_workspace(
    tmp_path: Path,
) -> None:
    """A swapped member path cannot redirect rollback outside the workspace."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "alpha"
    )
    outside_manifest = tmp_path.parent / f"{tmp_path.name}-outside-alpha.toml"
    fixture_write = bump_manifest_rewrite_application.write_atomic_text
    atomic_write = bump_pipeline_manifest_rewrites.bump_toml_files.write_atomic_text
    symlink_swapped = False
    outside_contents = b""

    def fail_second_fixture(
        path: Path,
        content: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Swap a changed member path outside the workspace before failing."""
        nonlocal outside_contents, symlink_swapped
        if path == case.fixtures[1].resolve():
            outside_contents = alpha_manifest.read_bytes()
            outside_manifest.write_bytes(outside_contents)
            alpha_manifest.unlink()
            alpha_manifest.symlink_to(outside_manifest)
            symlink_swapped = True
            raise OSError
        fixture_write(path, content, workspace_root=workspace_root)

    def bounded_rollback_write(
        path: Path,
        content: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Forward rollback through the original bounded atomic writer."""
        if symlink_swapped and path == alpha_manifest:
            assert workspace_root == case.root, (
                "rollback must revalidate the workspace boundary at write time"
            )
        atomic_write(path, content, workspace_root=workspace_root)

    try:
        with (
            mock.patch.object(
                bump_manifest_rewrite_application,
                "write_atomic_text",
                side_effect=fail_second_fixture,
            ),
            mock.patch.object(
                bump_pipeline_manifest_rewrites.bump_toml_files,
                "write_atomic_text",
                side_effect=bounded_rollback_write,
            ),
            pytest.raises(ManifestRewriteWriteError) as failure,
        ):
            bump.run(
                case.root,
                "1.2.3",
                options=bump.BumpOptions(
                    rebuild_lockfiles=False,
                    configuration=case.configuration,
                    workspace=case.workspace,
                ),
            )

        assert failure.value.failed_path == alpha_manifest, (
            "the rollback error should identify the member path rejected by the "
            "workspace boundary"
        )
        assert alpha_manifest.is_symlink(), (
            "rollback must leave the changed link in place"
        )
        assert outside_manifest.read_bytes() == outside_contents, (
            "rollback must not overwrite the outside target"
        )
    finally:
        if alpha_manifest.is_symlink():
            alpha_manifest.unlink()
        outside_manifest.unlink(missing_ok=True)
