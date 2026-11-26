"""Workspace-bound rollback for configured manifest rewrites."""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump_pipeline_manifest_restore,
)
from lading.commands.bump_manifest_rewrites import (
    ManifestRewriteWriteError,
)
from lading.commands.bump_toml_files import write_atomic_text
from lading.workspace import WorkspaceCrate, WorkspaceGraph
from tests.helpers.manifest_rewrite_recovery import (
    _make_partial_write_failure_case,
    _PartialWriteFailureCase,
    _run_partial_write_failure,
)

if typ.TYPE_CHECKING:
    from lading.commands.bump_toml_write_options import AtomicWriteOptions

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
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Forward rollback through the original bounded atomic writer."""
        assert write_options is not None, "rollback must supply its write boundary"
        if self.state.symlink_swapped and path == self.alpha_manifest:
            assert write_options.workspace_root == self.case.root, (
                "rollback must revalidate the workspace boundary at write time"
            )
        self.atomic_write(
            path,
            content,
            write_options,
        )

    def retarget_during_rollback_write(
        self,
        path: Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Swap the member destination immediately before rollback writes."""
        if path == self.alpha_manifest and not self.state.symlink_swapped:
            self.swap_member_path_outside(path)
        self.bounded_rollback_write(path, content, write_options)


def _exercise_rollback_swap(
    tmp_path: Path,
    *,
    swap_at_rollback: bool,
) -> _RollbackProbe:
    """Run a failed fixture batch whose rollback target moves outside the tree."""
    case = _make_partial_write_failure_case(tmp_path / "workspace")
    probe = _RollbackProbe(
        case=case,
        alpha_manifest=_alpha_manifest_path(case),
        outside_manifest=tmp_path / "outside-alpha.toml",
        atomic_write=write_atomic_text,
    )
    writer = (
        probe.retarget_during_rollback_write
        if swap_at_rollback
        else probe.bounded_rollback_write
    )
    try:
        with mock.patch.object(
            bump_pipeline_manifest_restore,
            "write_atomic_text",
            side_effect=writer,
        ):
            failure = _run_partial_write_failure(
                case,
                before_failure=(
                    None if swap_at_rollback else probe.swap_member_path_outside
                ),
            )

        assert isinstance(failure, ManifestRewriteWriteError), (
            "the fixture write failure should become a rollback error"
        )
        assert failure.failed_path == probe.alpha_manifest, (
            "the rollback error should identify the rejected member path"
        )
        assert probe.alpha_manifest.is_symlink(), (
            "rollback must leave the changed link in place"
        )
        assert probe.alpha_manifest.readlink() == probe.outside_manifest, (
            "rollback must not retarget the changed link"
        )
        assert probe.outside_manifest.read_bytes() == probe.state.outside_contents, (
            "rollback must not overwrite the outside target"
        )
    finally:
        if probe.alpha_manifest.is_symlink():
            probe.alpha_manifest.unlink()
        probe.outside_manifest.unlink(missing_ok=True)
    return probe


def test_rollback_does_not_follow_member_path_swapped_outside_workspace(
    tmp_path: Path,
) -> None:
    """A swapped member path cannot redirect rollback outside the workspace."""
    _exercise_rollback_swap(tmp_path, swap_at_rollback=False)


def test_rollback_revalidates_member_boundary_at_writer_time(
    tmp_path: Path,
) -> None:
    """A retarget immediately before the write cannot escape the workspace."""
    probe = _exercise_rollback_swap(tmp_path, swap_at_rollback=True)

    assert probe.state.symlink_swapped, (
        "the rollback writer callback must retarget the member path"
    )


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
    # Workspace discovery resolves Cargo metadata's manifest_path symlink.
    discovered_crates = tuple(
        WorkspaceCrate(
            name=crate.name,
            version=crate.version,
            manifest_path=(
                target_manifest if crate.name == "alpha" else crate.manifest_path
            ),
            root_path=crate.root_path,
            publish=crate.publish,
            readme_is_workspace=crate.readme_is_workspace,
            dependencies=crate.dependencies,
        )
        for crate in case.workspace.crates
    )
    case = dc.replace(
        case,
        workspace=WorkspaceGraph(
            workspace_root=case.workspace.workspace_root,
            crates=discovered_crates,
        ),
    )

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
    assert alpha_manifest.readlink() == target_manifest, (
        "rollback must preserve the symlink's original target"
    )


def _alpha_manifest_path(case: _PartialWriteFailureCase) -> Path:
    """Return the fixture workspace's alpha member manifest path."""
    return next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "alpha"
    )
