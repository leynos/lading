"""Tests for applying configured non-member manifest rewrites."""

import pathlib
import tempfile

import pytest

from lading.commands import bump_manifest_rewrite_application, bump_manifest_rewrites
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)


def test_unchanged_manifest_is_omitted_and_dry_run_does_not_write(
    tmp_path: pathlib.Path,
) -> None:
    """No-op plans are empty and dry-run application preserves bytes."""
    content = '[dependencies]\nalpha = "2.0.0"\n'
    manifest = tmp_path / "fixture/Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text(content, encoding="utf-8")
    original_bytes = manifest.read_bytes()
    group = ManifestRewriteConfig(paths=("fixture/Cargo.toml",))

    assert not plan_fixture_rewrites(
        tmp_path,
        (group,),
        manifest_rewrite_versions(target_version="2.0.0"),
    ), "an unchanged target version must produce no rewrite plan"
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(), content, '[dependencies]\nalpha = "3.0.0"\n'
    )

    changed_paths = bump_manifest_rewrites.apply_manifest_rewrites(
        (plan,), workspace_root=tmp_path, dry_run=True
    )

    assert changed_paths == (manifest.resolve(),), (
        "dry-run must return the planned manifest path"
    )
    assert manifest.read_bytes() == original_bytes, (
        "dry-run must preserve manifest bytes"
    )


def test_application_rejects_manifest_changed_after_planning(
    tmp_path: pathlib.Path,
) -> None:
    """Applying a stale plan must preserve the newer manifest content."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[dependencies]\nalpha = "1.0.0"\n', encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest,
        '[dependencies]\nalpha = "1.0.0"\n',
        '[dependencies]\nalpha = "2.0.0"\n',
    )
    manifest.write_text('[dependencies]\nalpha = "local-edit"\n', encoding="utf-8")

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="changed after planning",
    ) as failure:
        bump_manifest_rewrites.apply_manifest_rewrites(
            (plan,), workspace_root=tmp_path, dry_run=False
        )

    assert failure.value.failed_path == manifest, (
        "the error should identify the stale plan's target"
    )
    assert manifest.read_text(encoding="utf-8") == (
        '[dependencies]\nalpha = "local-edit"\n'
    ), "application must not overwrite changes made after planning"


def test_application_rejects_manifest_replaced_by_symlink_after_planning(
    tmp_path: pathlib.Path,
) -> None:
    """A symlink swapped in after planning cannot redirect an atomic write."""
    manifest = tmp_path / "Cargo.toml"
    original = '[dependencies]\nalpha = "1.0.0"\n'
    manifest.write_text(original, encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(),
        original,
        '[dependencies]\nalpha = "2.0.0"\n',
    )
    with tempfile.TemporaryDirectory(dir=tmp_path.parent) as outside_directory:
        outside = pathlib.Path(outside_directory) / "Cargo.toml"
        outside.write_text(original, encoding="utf-8")
        manifest.unlink()
        manifest.symlink_to(outside)

        with pytest.raises(
            bump_manifest_rewrites.ManifestRewriteError,
            match="no longer resolves to its planned path",
        ) as failure:
            bump_manifest_rewrites.apply_manifest_rewrites(
                (plan,), workspace_root=tmp_path, dry_run=False
            )

        assert failure.value.failed_path == plan.path, (
            "the stale destination error should identify the planned manifest path"
        )
        assert outside.read_text(encoding="utf-8") == original, (
            "application must not replace the symlink target outside the planned path"
        )


def test_manifest_rewrite_preserves_crlf_line_endings(
    tmp_path: pathlib.Path,
) -> None:
    """Planning and applying a rewrite retain a fixture's CRLF convention."""
    manifest = tmp_path / "fixtures" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    original = b'[dependencies]\r\nalpha = "1.0.0"\r\n'
    manifest.write_bytes(original)
    groups = (ManifestRewriteConfig(paths=("fixtures/Cargo.toml",)),)

    (plan,) = plan_fixture_rewrites(tmp_path, groups)
    bump_manifest_rewrites.apply_manifest_rewrites(
        (plan,), workspace_root=tmp_path, dry_run=False
    )

    assert manifest.read_bytes() == b'[dependencies]\r\nalpha = "2.0.0"\r\n', (
        "the rewritten manifest should retain CRLF line endings"
    )


def test_application_detects_line_ending_only_source_change(
    tmp_path: pathlib.Path,
) -> None:
    """A newline-only edit after planning invalidates the source snapshot."""
    manifest = tmp_path / "fixtures" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    original = b'[dependencies]\r\nalpha = "1.0.0"\r\n'
    manifest.write_bytes(original)
    groups = (ManifestRewriteConfig(paths=("fixtures/Cargo.toml",)),)
    plans = plan_fixture_rewrites(tmp_path, groups)
    manifest.write_bytes(original.replace(b"\r\n", b"\n"))

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="changed after planning",
    ):
        bump_manifest_rewrites.apply_manifest_rewrites(
            plans, workspace_root=tmp_path, dry_run=False
        )

    assert manifest.read_bytes() == original.replace(b"\r\n", b"\n"), (
        "application should preserve the external line-ending edit"
    )


def test_atomic_write_rechecks_containment_after_plan_source_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final-component symlink swap cannot redirect a bounded write."""
    manifest = tmp_path / "Cargo.toml"
    original = '[dependencies]\nalpha = "1.0.0"\n'
    manifest.write_text(original, encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(),
        original,
        '[dependencies]\nalpha = "2.0.0"\n',
    )
    outside = tmp_path.parent / f"{tmp_path.name}-outside-Cargo.toml"
    outside.write_text(original, encoding="utf-8")
    write_text = bump_manifest_rewrite_application.write_atomic_text

    def redirect_before_write(
        file_path: pathlib.Path,
        content: str,
        *,
        workspace_root: pathlib.Path | None = None,
    ) -> None:
        """Swap the checked destination after the application source read."""
        file_path.unlink()
        file_path.symlink_to(outside)
        write_text(file_path, content, workspace_root=workspace_root)

    monkeypatch.setattr(
        bump_manifest_rewrite_application,
        "write_atomic_text",
        redirect_before_write,
    )
    try:
        with pytest.raises(
            bump_manifest_rewrites.ManifestRewriteWriteError,
            match="must stay within the workspace",
        ) as failure:
            bump_manifest_rewrites.apply_manifest_rewrites(
                (plan,), workspace_root=tmp_path, dry_run=False
            )

        assert failure.value.failed_path == plan.path, (
            "the bounded writer failure should identify the planned manifest"
        )
        assert outside.read_text(encoding="utf-8") == original, (
            "a redirected destination must not modify the outside file"
        )
        assert manifest.is_symlink(), "the racing symlink must not be replaced"
    finally:
        outside.unlink(missing_ok=True)
