"""Ensure Ctrl-C restores completed manifest writes in a version bump."""

from pathlib import Path
from unittest import mock

import pytest

from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_manifests,
    bump_pipeline,
)
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case


def test_member_interruption_restores_completed_manifest_writes(
    tmp_path: Path,
) -> None:
    """An interrupt during member updates restores earlier successful writes."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    failed_manifest = case.workspace.crates[-1].manifest_path
    original_update_manifest = bump_pipeline._update_manifest
    completed_member_writes: list[Path] = []

    def interrupt_selected_manifest(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        update_context: bump_manifests._ManifestUpdateContext,
    ) -> bump_manifests._ManifestUpdateResult:
        """Interrupt after earlier members have had a chance to update."""
        if manifest_path == failed_manifest:
            raise KeyboardInterrupt
        result = original_update_manifest(
            manifest_path,
            selectors,
            target_version,
            update_context,
        )
        if result.was_changed:
            completed_member_writes.append(manifest_path)
        return result

    with (
        mock.patch.object(
            bump_pipeline,
            "_update_manifest",
            side_effect=interrupt_selected_manifest,
        ),
        pytest.raises(KeyboardInterrupt),
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

    assert case.workspace.crates[0].manifest_path in completed_member_writes, (
        "the first member must be changed before the injected interruption"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "member interruption must restore every completed workspace manifest write"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "member interruption must leave not-yet-applied fixture rewrites untouched"
    )


def test_non_member_interruption_restores_completed_rewrites_and_members(
    tmp_path: Path,
) -> None:
    """An interrupt during fixture application rolls back completed writes."""
    case = _make_partial_write_failure_case(tmp_path)
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text
    completed_fixture_writes: list[Path] = []

    def interrupt_second_fixture(
        path: Path,
        content: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Write the first fixture, then interrupt before the second one."""
        if path == case.fixtures[1].resolve():
            raise KeyboardInterrupt
        write_atomic_text(path, content, workspace_root=workspace_root)
        completed_fixture_writes.append(path)

    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=interrupt_second_fixture,
        ),
        pytest.raises(KeyboardInterrupt),
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

    assert completed_fixture_writes == [case.fixtures[0].resolve()], (
        "the first fixture must be written before the injected interruption"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "interruption recovery must restore every completed fixture rewrite"
    )
    assert {
        path: path.read_bytes() for path in case.workspace_manifests
    } == case.workspace_manifests, (
        "interruption recovery must restore member manifests written earlier"
    )
