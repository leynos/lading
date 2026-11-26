"""Reject fixture plans whose workspace source versions changed after planning."""

import typing as typ
from pathlib import Path
from unittest import mock

import pytest
from tomlkit import parse as parse_toml

from lading.commands import (
    bump,
    bump_manifests,
    bump_pipeline,
    bump_pipeline_manifest_rewrites,
    bump_pipeline_manifest_sources,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.exceptions import LadingError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case
from tests.helpers.manifest_rewrite_writes import record_workspace_write_attempts
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
    write_attempts = record_workspace_write_attempts(monkeypatch, case.root)
    with (
        mock.patch.object(
            bump_pipeline_manifest_rewrites,
            "_plan_non_member_manifest_rewrites",
            side_effect=plan_then_change_source,
        ),
        pytest.raises(
            ManifestRewriteError,
            match="changed after manifest rewrite planning",
        ),
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
    assert not write_attempts, "stale version validation must precede all writes"
    assert not repository.regenerated, "a stale plan must stop before lockfile work"


def test_renamed_member_package_is_rejected_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member renamed after planning cannot feed a cached fixture plan."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    renamed_text = '[package]\nname = "renamed-alpha"\nversion = "0.1.0"\n'
    fixture_original = {path: path.read_bytes() for path in case.fixtures}
    repository = RecordingLockfileRepository()
    write_attempts = record_workspace_write_attempts(monkeypatch, case.root)
    original_plan = bump_pipeline_manifest_rewrites._plan_non_member_manifest_rewrites

    def plan_then_rename_package(
        context: _BumpContext,
        target_version: str,
    ) -> _ManifestRewriteStage:
        """Rename the member package after capturing its rewrite plan."""
        stage = original_plan(context, target_version)
        alpha_manifest.write_text(renamed_text, encoding="utf-8")
        return stage

    with (
        mock.patch.object(
            bump_pipeline_manifest_rewrites,
            "_plan_non_member_manifest_rewrites",
            side_effect=plan_then_rename_package,
        ),
        pytest.raises(
            ManifestRewriteError,
            match="package name differs from workspace discovery",
        ) as failure,
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

    assert failure.value.failed_path == alpha_manifest.resolve(), (
        "the renamed manifest must be named in the failure"
    )
    assert alpha_manifest.read_text(encoding="utf-8") == renamed_text, (
        "the external rename must remain in place"
    )
    assert (case.root / "Cargo.toml").read_bytes() == case.workspace_manifests[
        case.root / "Cargo.toml"
    ], "name validation must run before the workspace manifest is written"
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_original, (
        "rewrite plans must not use a manifest renamed after discovery"
    )
    assert not write_attempts, "name validation must precede all writes"
    assert not repository.regenerated, "a renamed member must stop lockfile work"


def test_missing_package_version_before_planning_conflicts_with_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing source version cannot mask stale workspace discovery data."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    alpha_manifest.write_text('[package]\nname = "alpha"\n', encoding="utf-8")
    root_original = case.workspace_manifests[case.root / "Cargo.toml"]
    fixture_original = {path: path.read_bytes() for path in case.fixtures}
    repository = RecordingLockfileRepository()
    write_attempts = record_workspace_write_attempts(monkeypatch, case.root)

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
    assert not write_attempts, "planning conflicts must precede all writes"
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
    write_attempts = record_workspace_write_attempts(monkeypatch, case.root)
    original_plan = bump_pipeline_manifest_rewrites._plan_non_member_manifest_rewrites

    def plan_then_remove_version(
        context: _BumpContext,
        target_version: str,
    ) -> _ManifestRewriteStage:
        """Remove a package version after its fixture rewrite plan is captured."""
        stage = original_plan(context, target_version)
        alpha_manifest.write_text('[package]\nname = "alpha"\n', encoding="utf-8")
        return stage

    with (
        mock.patch.object(
            bump_pipeline_manifest_rewrites,
            "_plan_non_member_manifest_rewrites",
            side_effect=plan_then_remove_version,
        ),
        pytest.raises(
            ManifestRewriteError,
            match="changed after manifest rewrite planning",
        ),
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
    assert not write_attempts, "stale version validation must precede all writes"
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

    with (
        mock.patch.object(
            bump_manifests, "_update_manifest", side_effect=edit_alpha_before_update
        ),
        pytest.raises(LadingError, match="package version changed"),
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


def test_member_version_is_rechecked_before_fixture_rewrite(
    tmp_path: Path,
) -> None:
    """A member version edit after its write prevents cached fixture plans."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    beta_manifest = tmp_path / "crates/beta/Cargo.toml"
    fixture_original = {path: path.read_bytes() for path in case.fixtures}
    original_members = {
        path: case.workspace_manifests[path] for path in (alpha_manifest, beta_manifest)
    }
    original_crate_stage = bump_pipeline._process_crate_manifests
    repository = RecordingLockfileRepository()

    def edit_member_after_updates(
        context: _BumpContext,
        target_version: str,
        stage: _ManifestRewriteStage,
        changed_manifests: set[Path],
    ) -> None:
        """Change alpha only after all normal member writes have succeeded."""
        original_crate_stage(context, target_version, stage, changed_manifests)
        assert 'version = "1.2.3"' in alpha_manifest.read_text(encoding="utf-8"), (
            "alpha must be bumped before the external version edit"
        )
        assert 'version = "1.2.3"' in beta_manifest.read_text(encoding="utf-8"), (
            "beta must be bumped before fixture-stage validation"
        )
        alpha_manifest.write_text(
            '[package]\nname = "alpha"\nversion = "0.2.0"\n',
            encoding="utf-8",
        )

    with (
        mock.patch.object(
            bump_pipeline,
            "_process_crate_manifests",
            side_effect=edit_member_after_updates,
        ),
        pytest.raises(ManifestRewriteError, match="changed after member manifest"),
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

    assert 'version = "0.2.0"' in alpha_manifest.read_text(encoding="utf-8"), (
        "the external package version edit must remain in place"
    )
    assert beta_manifest.read_bytes() == original_members[beta_manifest], (
        "uncontested member writes must roll back after stale-version detection"
    )
    assert (case.root / "Cargo.toml").read_bytes() == case.workspace_manifests[
        case.root / "Cargo.toml"
    ], "the workspace write must roll back before fixture application"
    assert {path: path.read_bytes() for path in case.fixtures} == fixture_original, (
        "fixture rewrites must not use a stale post-bump member version"
    )
    assert not repository.regenerated, "stale members must stop lockfile work"


def test_unreadable_snapshot_is_reported_before_planning(
    tmp_path: Path,
) -> None:
    """A snapshot that cannot be read stops planning with its path."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    manifest.chmod(0o000)
    try:
        with pytest.raises(
            ManifestRewriteError,
            match="Could not snapshot workspace manifest",
        ) as failure:
            bump_pipeline_manifest_sources._read_workspace_manifest_snapshots(
                manifest, ()
            )
    finally:
        manifest.chmod(0o600)

    assert failure.value.failed_path == manifest, (
        "the unreadable manifest must be named in the failure"
    )


def test_inherited_version_requires_a_workspace_version(tmp_path: Path) -> None:
    """An inherited package version without a workspace value is rejected."""
    workspace_manifest = tmp_path / "Cargo.toml"
    document = parse_toml("[package]\nversion.workspace = true\n")

    with pytest.raises(
        ManifestRewriteError, match="Workspace-inherited package version"
    ):
        bump_pipeline_manifest_sources._crate_manifest_version(
            document, None, workspace_manifest
        )


def test_unparseable_snapshot_is_reported(tmp_path: Path) -> None:
    """A malformed captured manifest stops planning with its path."""
    manifest = tmp_path / "Cargo.toml"

    with pytest.raises(ManifestRewriteError, match="Could not parse manifest snapshot"):
        bump_pipeline_manifest_sources._parse_manifest_snapshot(
            manifest, "[package\nname = broken\n"
        )


def test_unresolvable_root_manifest_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root manifest that cannot be resolved is reported."""
    case = _make_partial_write_failure_case(tmp_path)
    root_manifest = case.root / "Cargo.toml"
    context = bump._initialize_bump_context(
        case.root,
        bump.BumpOptions(configuration=case.configuration, workspace=case.workspace),
    )
    original_resolve = Path.resolve

    def reject_root_resolution(path: Path, *, strict: bool = False) -> Path:
        """Fail resolution only for the workspace root manifest entry."""
        if path == root_manifest:
            message = "resolution failed"
            raise OSError(message)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", reject_root_resolution)

    with pytest.raises(
        ManifestRewriteError, match="Could not resolve workspace root manifest"
    ) as failure:
        bump_pipeline_manifest_sources._validate_workspace_manifest_entry(context)

    assert failure.value.failed_path == root_manifest, (
        "the unresolved manifest entry must be named in the failure"
    )
