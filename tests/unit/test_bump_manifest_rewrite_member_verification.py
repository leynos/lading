"""Verify member manifests after the bump's own writes."""

import os
from pathlib import Path

import pytest

from lading.commands import (
    bump,
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_rewrites,
    bump_toml_files,
    bump_toml_write_options,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case


def test_reused_workspace_graph_rejects_versions_changed_after_discovery(
    tmp_path: Path,
) -> None:
    """A stale workspace graph cannot authorize rewrites from different sources."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("workspace-bounded atomic writes are unavailable")
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = next(
        crate.manifest_path for crate in case.workspace.crates if crate.name == "alpha"
    )
    alpha_text = alpha_manifest.read_text(encoding="utf-8")
    alpha_manifest.write_text(
        alpha_text.replace('version = "0.1.0"', "version.workspace = true", 1),
        encoding="utf-8",
    )
    assert "version.workspace = true" in alpha_manifest.read_text(encoding="utf-8"), (
        "the fixture must exercise an inherited workspace version"
    )
    bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=case.configuration,
            workspace=case.workspace,
        ),
    )
    bumped_workspace = {path: path.read_bytes() for path in case.workspace_manifests}
    bumped_fixtures = {path: path.read_bytes() for path in case.fixtures}

    with pytest.raises(ManifestRewriteError, match="workspace discovery"):
        bump.run(
            case.root,
            "1.2.4",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )

    assert {path: path.read_bytes() for path in case.workspace_manifests} == (
        bumped_workspace
    ), "a stale graph must be rejected before changing workspace manifests"
    assert {path: path.read_bytes() for path in case.fixtures} == bumped_fixtures, (
        "a stale graph must not plan fixture strings from another source version"
    )


def test_member_manifest_verification_reports_an_unreadable_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member manifest that cannot be re-read is reported with its path."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("written text", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=tmp_path,
        written_manifest_contents={manifest_path: "written text"},
        written_manifest_identities={
            manifest_path: bump_toml_write_options.AtomicWriteIdentity(1, 2)
        },
    )

    def reject_read(*_args: object, **_kwargs: object) -> tuple[str, object]:
        """Simulate an unreadable manifest during revalidation."""
        message = "read failed"
        raise OSError(message)

    monkeypatch.setattr(
        bump_pipeline_manifest_recovery, "_read_bounded_regular_text", reject_read
    )

    with pytest.raises(
        bump_pipeline_manifest_recovery.ManifestRewriteError,
        match="Could not verify manifest after an earlier bump pass",
    ):
        bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
            manifest_path, stage
        )


def test_member_manifest_verification_reports_a_changed_identity(
    tmp_path: Path,
) -> None:
    """A member manifest replaced since the bump's write is reported."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("written text", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=tmp_path,
        written_manifest_contents={manifest_path: "written text"},
        written_manifest_identities={
            manifest_path: bump_toml_write_options.AtomicWriteIdentity(1, 2)
        },
    )

    with pytest.raises(
        bump_pipeline_manifest_recovery.ManifestRewriteError,
        match="replacement identity changed",
    ):
        bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
            manifest_path, stage
        )
    assert manifest_path.read_text(encoding="utf-8") == "written text", (
        "verification must not alter the changed manifest"
    )


def test_member_manifest_verification_reports_changed_contents(
    tmp_path: Path,
) -> None:
    """A member manifest edited since the bump's write is reported."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("edited text", encoding="utf-8")
    with manifest_path.open("rb") as handle:
        metadata = os.fstat(handle.fileno())
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=tmp_path,
        written_manifest_contents={manifest_path: "written text"},
        written_manifest_identities={
            manifest_path: bump_toml_write_options.AtomicWriteIdentity(
                metadata.st_dev, metadata.st_ino
            )
        },
    )

    with pytest.raises(
        bump_pipeline_manifest_recovery.ManifestRewriteError,
        match="Manifest changed after an earlier bump pass",
    ):
        bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
            manifest_path, stage
        )


def test_member_manifest_verification_reports_an_unresolvable_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member manifest whose destination cannot be resolved is reported."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("written text", encoding="utf-8")
    stage = bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=tmp_path,
        written_manifest_contents={manifest_path: "written text"},
        written_manifest_destinations={manifest_path: manifest_path},
    )
    original_resolve = Path.resolve

    def reject_resolution(path: Path, *, strict: bool = False) -> Path:
        """Fail resolution only for the verified member manifest."""
        if path == manifest_path:
            message = "resolution failed"
            raise OSError(message)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", reject_resolution)

    with pytest.raises(
        bump_pipeline_manifest_recovery.ManifestRewriteError,
        match="Could not verify manifest destination",
    ):
        bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
            manifest_path, stage
        )
