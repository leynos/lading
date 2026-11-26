"""Reject stale, malformed, or renamed manifest sources before planning."""

import typing as typ
from pathlib import Path
from unittest import mock

import pytest
from tomlkit import parse as parse_toml

from lading.commands import (
    bump,
    bump_pipeline_manifest_rewrites,
    bump_pipeline_manifest_sources,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case
from tests.helpers.manifest_rewrite_writes import record_workspace_write_attempts
from tests.helpers.recording_lockfile_repository import RecordingLockfileRepository

if typ.TYPE_CHECKING:
    from lading.commands.bump_manifests import _BumpContext
    from lading.commands.bump_pipeline_manifest_rewrites import (
        _ManifestRewriteStage,
    )


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


def test_unreadable_snapshot_is_reported_before_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A snapshot that cannot be read stops planning with its path."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    original_read_text = Path.read_text

    def reject_manifest_read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        """Fail only the snapshot read for the configured manifest."""
        if path == manifest:
            message = "read failed"
            raise PermissionError(message)
        return original_read_text(
            path, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "read_text", reject_manifest_read)

    with pytest.raises(
        ManifestRewriteError,
        match="Could not snapshot workspace manifest",
    ) as failure:
        bump_pipeline_manifest_sources._read_workspace_manifest_snapshots(manifest, ())

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
