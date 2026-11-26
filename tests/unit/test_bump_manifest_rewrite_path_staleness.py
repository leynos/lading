"""Reject root and member manifest destination changes during a bump."""

import typing as typ
from pathlib import Path

import pytest

from lading.commands import bump, bump_pipeline
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.exceptions import LadingError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case
from tests.helpers.manifest_rewrite_writes import record_workspace_write_attempts

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifests import _BumpContext
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


def test_member_destination_is_pinned_to_workspace_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member symlink retargeted after discovery is rejected before writing."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    redirected_manifest = tmp_path / "crates/redirected/Cargo.toml"
    redirected_manifest.parent.mkdir(parents=True)
    redirected_manifest.write_bytes(alpha_manifest.read_bytes())
    alpha_manifest.unlink()
    alpha_manifest.symlink_to(redirected_manifest)
    redirected_original = redirected_manifest.read_bytes()
    root_original = case.workspace_manifests[case.root / "Cargo.toml"]
    write_attempts = record_workspace_write_attempts(monkeypatch, case.root)

    with pytest.raises(LadingError, match="destination changed after bump context"):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert alpha_manifest.is_symlink(), "the changed member alias must remain"
    assert redirected_manifest.read_bytes() == redirected_original, (
        "the retargeted member file must not receive the bump"
    )
    assert redirected_manifest not in {path.resolve() for path in write_attempts}, (
        "no atomic writer may receive the redirected manifest destination"
    )
    assert (case.root / "Cargo.toml").read_bytes() == root_original, (
        "the prior workspace update must roll back after destination rejection"
    )


def test_root_manifest_entry_is_rechecked_before_fixture_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root manifest alias cannot redirect cached fixture version plans."""
    case = _make_partial_write_failure_case(tmp_path)
    root_entry = case.root / "Cargo.toml"
    canonical_manifest = case.root / "workspace-original.toml"
    root_entry.rename(canonical_manifest)
    try:
        root_entry.symlink_to(canonical_manifest.name)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")
    alternate_manifest = case.root / "alternate-workspace.toml"
    alternate_manifest.write_bytes(canonical_manifest.read_bytes())
    canonical_original = canonical_manifest.read_bytes()
    fixture_originals = {path: path.read_bytes() for path in case.fixtures}
    original_crate_stage = bump_pipeline._process_crate_manifests

    def update_members_then_retarget_root(
        context: _BumpContext,
        target_version: str,
        stage: _ManifestRewriteStage,
        changed_manifests: set[Path],
    ) -> None:
        """Retarget the root entry after workspace and member writes complete."""
        original_crate_stage(context, target_version, stage, changed_manifests)
        root_entry.unlink()
        root_entry.symlink_to(alternate_manifest.name)

    monkeypatch.setattr(
        bump_pipeline,
        "_process_crate_manifests",
        update_members_then_retarget_root,
    )

    with pytest.raises(
        ManifestRewriteError,
        match="Workspace root manifest destination changed during bump",
    ) as failure:
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert failure.value.failed_path == root_entry, (
        "the error should identify the retargeted lexical root manifest"
    )
    assert root_entry.resolve(strict=True) == alternate_manifest, (
        "the externally retargeted root entry must remain in place"
    )
    assert canonical_manifest.read_bytes() == canonical_original, (
        "the canonical workspace manifest write must roll back"
    )
    assert alternate_manifest.read_bytes() == canonical_original, (
        "the alternate root target must not receive a bump"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_originals, (
        "cached fixture rewrites must not be applied after the root entry changes"
    )
