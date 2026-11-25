"""Workspace-bound rollback for configured manifest rewrites."""

import collections.abc as cabc
import dataclasses as dc
from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump_pipeline_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrites import (
    ManifestRewriteWriteError,
)
from tests.helpers.manifest_rewrite_recovery import (
    _make_partial_write_failure_case,
    _PartialWriteFailureCase,
    _run_partial_write_failure,
)

pytestmark = pytest.mark.usefixtures("reset_process_metrics")


@dc.dataclass(slots=True)
class _RollbackProbeState:
    """Track the symlink swap and outside bytes for one failure scenario."""

    symlink_swapped: bool = False
    outside_contents: bytes = b""


@dc.dataclass(slots=True)
class _RollbackProbe:
    """Inject a fixture failure and inspect bounded workspace restoration."""

    case: _PartialWriteFailureCase
    alpha_manifest: Path
    outside_manifest: Path
    atomic_write: cabc.Callable[..., None]
    state: _RollbackProbeState = dc.field(default_factory=_RollbackProbeState)

    def swap_member_path_outside(self, _path: Path) -> None:
        """Swap the already-updated member path outside before fixture failure."""
        self.state.outside_contents = self.alpha_manifest.read_bytes()
        self.outside_manifest.write_bytes(self.state.outside_contents)
        self.alpha_manifest.unlink()
        self.alpha_manifest.symlink_to(self.outside_manifest)
        self.state.symlink_swapped = True

    def bounded_rollback_write(
        self,
        path: Path,
        content: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Forward rollback through the original bounded atomic writer."""
        if self.state.symlink_swapped and path == self.alpha_manifest:
            assert workspace_root == self.case.root, (
                "rollback must revalidate the workspace boundary at write time"
            )
        self.atomic_write(path, content, workspace_root=workspace_root)


def test_rollback_does_not_follow_member_path_swapped_outside_workspace(
    tmp_path: Path,
) -> None:
    """A swapped member path cannot redirect rollback outside the workspace."""
    case = _make_partial_write_failure_case(tmp_path / "workspace")
    alpha_manifest = _alpha_manifest_path(case)
    outside_manifest = tmp_path / "outside-alpha.toml"
    probe = _RollbackProbe(
        case=case,
        alpha_manifest=alpha_manifest,
        outside_manifest=outside_manifest,
        atomic_write=bump_pipeline_manifest_rewrites.bump_toml_files.write_atomic_text,
    )

    try:
        with mock.patch.object(
            bump_pipeline_manifest_rewrites.bump_toml_files,
            "write_atomic_text",
            side_effect=probe.bounded_rollback_write,
        ):
            failure = _run_partial_write_failure(
                case,
                before_failure=probe.swap_member_path_outside,
            )

        assert isinstance(failure, ManifestRewriteWriteError), (
            "the fixture write failure should become a rollback error"
        )
        assert failure.failed_path == alpha_manifest, (
            "the rollback error should identify the member path rejected by the "
            "workspace boundary"
        )
        assert alpha_manifest.is_symlink(), (
            "rollback must leave the changed link in place"
        )
        assert outside_manifest.read_bytes() == probe.state.outside_contents, (
            "rollback must not overwrite the outside target"
        )
    finally:
        if alpha_manifest.is_symlink():
            alpha_manifest.unlink()
        outside_manifest.unlink(missing_ok=True)


def test_rollback_restores_original_target_for_workspace_member_symlink(
    tmp_path: Path,
) -> None:
    """Rollback restores the captured in-workspace target of a member link."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = _alpha_manifest_path(case)
    original_contents = alpha_manifest.read_bytes()
    target_manifest = alpha_manifest.parent / "manifest-target" / "Cargo.toml"
    target_manifest.parent.mkdir()
    alpha_manifest.replace(target_manifest)
    alpha_manifest.symlink_to(target_manifest)

    def assert_member_was_bumped(_path: Path) -> None:
        """Check the linked target changed before the injected fixture failure."""
        assert b'version = "1.2.3"' in target_manifest.read_bytes(), (
            "the linked member target must be bumped before rollback is exercised"
        )

    failure = _run_partial_write_failure(case, before_failure=assert_member_was_bumped)

    assert failure.failed_path == case.fixtures[1].resolve(), (
        "the original fixture failure should remain the reported error"
    )
    assert target_manifest.read_bytes() == original_contents, (
        "rollback must restore the actual member manifest target"
    )
    assert alpha_manifest.is_symlink(), "the original member link should remain"


def _alpha_manifest_path(case: _PartialWriteFailureCase) -> Path:
    """Return the fixture workspace's alpha member manifest path."""
    return next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "alpha"
    )
