"""Reject fixture plans whose workspace source versions changed after planning."""

import typing as typ
from pathlib import Path

import pytest

from lading.commands import (
    bump,
    bump_manifests,
    bump_pipeline_manifest_rewrites,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.exceptions import LadingError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifests import _BumpContext
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


def test_changed_inherited_source_version_aborts_before_workspace_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale fixture plan cannot proceed after its inherited source changes."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    root_manifest = tmp_path / "Cargo.toml"
    alpha_manifest.write_text(
        '[package]\nname = "alpha"\nversion.workspace = true\n',
        encoding="utf-8",
    )
    original_plan = bump_pipeline_manifest_rewrites._plan_non_member_manifest_rewrites

    def plan_then_change_source(
        context: _BumpContext,
        target_version: str,
    ) -> _ManifestRewriteStage:
        """Change an inherited workspace version after capturing the plan."""
        stage = original_plan(context, target_version)
        root_text = root_manifest.read_text(encoding="utf-8")
        root_manifest.write_text(
            root_text.replace('version = "0.1.0"', 'version = "0.2.0"'),
            encoding="utf-8",
        )
        return stage

    original_fixture = {path: path.read_bytes() for path in case.fixtures}
    repository = RecordingLockfileRepository()
    monkeypatch.setattr(
        bump_pipeline_manifest_rewrites,
        "_plan_non_member_manifest_rewrites",
        plan_then_change_source,
    )

    with pytest.raises(
        ManifestRewriteError,
        match="changed after manifest rewrite planning",
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=True,
                configuration=case.configuration,
                workspace=case.workspace,
                lockfile_repository=repository,
            ),
        )

    assert 'version = "0.2.0"' in root_manifest.read_text(encoding="utf-8"), (
        "the external workspace edit must remain in place"
    )
    assert "version.workspace = true" in alpha_manifest.read_text(encoding="utf-8"), (
        "the inherited member source must remain as edited"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == original_fixture, (
        "the stale fixture plan must not be applied"
    )
    assert not repository.regenerated, "a stale plan must stop before lockfile work"


def test_missing_package_version_before_planning_conflicts_with_discovery(
    tmp_path: Path,
) -> None:
    """A missing source version cannot mask stale workspace discovery data."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    alpha_manifest.write_text('[package]\nname = "alpha"\n', encoding="utf-8")
    root_original = case.workspace_manifests[case.root / "Cargo.toml"]
    fixture_original = {path: path.read_bytes() for path in case.fixtures}
    repository = RecordingLockfileRepository()

    with pytest.raises(ManifestRewriteError, match="workspace discovery"):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=True,
                configuration=case.configuration,
                workspace=case.workspace,
                lockfile_repository=repository,
            ),
        )

    assert (case.root / "Cargo.toml").read_bytes() == root_original, (
        "the mismatch must fail before the workspace manifest is written"
    )
    assert (
        alpha_manifest.read_text(encoding="utf-8") == '[package]\nname = "alpha"\n'
    ), "the externally removed version must remain in place"
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_original, (
        "rewrite planning must not use a missing version as cached metadata"
    )
    assert not repository.regenerated, "a planning mismatch must stop lockfile work"


def test_removed_package_version_after_planning_rejects_stale_fixture_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A removed source version cannot fall back to cached workspace metadata."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    original_alpha = alpha_manifest.read_bytes()
    fixture_original = {path: path.read_bytes() for path in case.fixtures}
    repository = RecordingLockfileRepository()
    original_plan = bump_pipeline_manifest_rewrites._plan_non_member_manifest_rewrites

    def plan_then_remove_version(
        context: _BumpContext,
        target_version: str,
    ) -> _ManifestRewriteStage:
        """Remove a package version after its fixture rewrite plan is captured."""
        stage = original_plan(context, target_version)
        alpha_manifest.write_text('[package]\nname = "alpha"\n', encoding="utf-8")
        return stage

    monkeypatch.setattr(
        bump_pipeline_manifest_rewrites,
        "_plan_non_member_manifest_rewrites",
        plan_then_remove_version,
    )

    with pytest.raises(
        ManifestRewriteError,
        match="changed after manifest rewrite planning",
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=True,
                configuration=case.configuration,
                workspace=case.workspace,
                lockfile_repository=repository,
            ),
        )

    assert (
        alpha_manifest.read_text(encoding="utf-8") == '[package]\nname = "alpha"\n'
    ), "the externally removed version must remain in place"
    assert (case.root / "Cargo.toml").read_bytes() == case.workspace_manifests[
        case.root / "Cargo.toml"
    ], "stale source detection must happen before workspace writes"
    assert alpha_manifest.read_bytes() != original_alpha, (
        "the test must retain the external package version removal"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_original, (
        "fixture rewrites must not use cached metadata after the source is removed"
    )
    assert not repository.regenerated, "a planning mismatch must stop lockfile work"


def test_changed_member_version_is_rejected_before_its_manifest_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member version changed after planning cannot feed a stale fixture plan."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    fixture_original = {path: path.read_bytes() for path in case.fixtures}
    original_update = bump_manifests._update_manifest
    repository = RecordingLockfileRepository()

    def edit_alpha_before_update(
        manifest_path: Path,
        selectors: tuple[tuple[str, ...], ...],
        target_version: str,
        update_context: bump_manifests._ManifestUpdateContext,
    ) -> bump_manifests._ManifestUpdateResult:
        """Insert an external version change just before the member is parsed."""
        if manifest_path == alpha_manifest:
            manifest_path.write_text(
                '[package]\nname = "alpha"\nversion = "0.2.0"\n',
                encoding="utf-8",
            )
        return original_update(manifest_path, selectors, target_version, update_context)

    monkeypatch.setattr(bump_manifests, "_update_manifest", edit_alpha_before_update)

    with pytest.raises(LadingError, match="package version changed"):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=True,
                configuration=case.configuration,
                workspace=case.workspace,
                lockfile_repository=repository,
            ),
        )

    assert 'version = "0.2.0"' in alpha_manifest.read_text(encoding="utf-8"), (
        "the external member edit must remain in place"
    )
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_original, (
        "fixture strings must not use a member version changed after planning"
    )
    assert (
        case.workspace_manifests[case.root / "Cargo.toml"]
        == (case.root / "Cargo.toml").read_bytes()
    ), "the earlier workspace write must still roll back"
    assert not repository.regenerated, "a changed source must stop lockfile work"


def test_member_destination_is_pinned_to_workspace_discovery(
    tmp_path: Path,
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
    assert (case.root / "Cargo.toml").read_bytes() == root_original, (
        "the prior workspace update must roll back after destination rejection"
    )
