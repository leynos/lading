"""Tests for applying configured non-member manifest rewrites."""

import pathlib

import pytest

from lading.commands import (
    bump_manifest_rewrite_application,
    bump_manifest_rewrites,
    bump_toml_files,
)
from lading.commands.bump_toml_write_options import (
    AtomicWriteOptions,
    AtomicWriteStatus,
)
from lading.config import ManifestRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

COMPLETION_CALLBACK_FAILURE = "completion callback failed"


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


def test_application_reports_destination_removed_after_planning(
    tmp_path: pathlib.Path,
) -> None:
    """A missing destination becomes a path-specific rewrite error."""
    manifest = tmp_path / "Cargo.toml"
    original = '[dependencies]\nalpha = "1.0.0"\n'
    manifest.write_text(original, encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(), original, '[dependencies]\nalpha = "2.0.0"\n'
    )
    manifest.unlink()

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not resolve manifest rewrite target",
    ) as failure:
        bump_manifest_rewrites.apply_manifest_rewrites(
            (plan,), workspace_root=tmp_path, dry_run=False
        )

    assert failure.value.failed_path == plan.path, (
        "the missing destination error should identify the planned manifest"
    )
    assert failure.value.__cause__ is not None, (
        "the filesystem resolution error should remain available as the cause"
    )


def test_application_reports_source_read_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source read failure carries its path and filesystem cause."""
    manifest = tmp_path / "Cargo.toml"
    original = '[dependencies]\nalpha = "1.0.0"\n'
    manifest.write_text(original, encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(), original, '[dependencies]\nalpha = "2.0.0"\n'
    )

    def fail_manifest_read(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        """Reject source reads with a filesystem permission error."""
        _ = path, encoding, errors, newline
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "read_text", fail_manifest_read)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not read manifest rewrite target",
    ) as failure:
        bump_manifest_rewrites.apply_manifest_rewrites(
            (plan,), workspace_root=tmp_path, dry_run=False
        )

    assert failure.value.failed_path == plan.path, (
        "the failed source read should identify the planned manifest"
    )
    assert isinstance(failure.value.__cause__, PermissionError), (
        "the source permission error should remain available as the cause"
    )


def test_application_reports_manifest_persisted_when_completion_callback_fails(
    tmp_path: pathlib.Path,
) -> None:
    """Progress includes the replacement when its completion callback fails."""
    _require_bounded_write_support()
    manifest = tmp_path / "Cargo.toml"
    original = '[dependencies]\nalpha = "1.0.0"\n'
    replacement = '[dependencies]\nalpha = "2.0.0"\n'
    manifest.write_text(original, encoding="utf-8")
    status = AtomicWriteStatus()
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(), original, replacement
    )

    def record_before_write(
        _path: pathlib.Path,
        _original_text: str,
        _new_text: str,
        _destination: pathlib.Path,
    ) -> AtomicWriteStatus:
        """Expose the status that the atomic writer updates on replacement."""
        return status

    def fail_after_write(_path: pathlib.Path) -> None:
        """Fail confirmation after the new manifest is already persisted."""
        raise OSError(COMPLETION_CALLBACK_FAILURE)

    callbacks = bump_manifest_rewrite_application._ManifestRewriteWriteCallbacks(
        before_write=record_before_write,
        after_write=fail_after_write,
    )
    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteWriteError,
        match="was written, but its completion callback failed",
    ) as failure:
        bump_manifest_rewrite_application.apply_manifest_rewrites(
            (plan,),
            workspace_root=tmp_path,
            dry_run=False,
            write_callbacks=callbacks,
        )

    assert status.replacement_completed, (
        "the writer should record replacement before completion callbacks run"
    )
    assert failure.value.completed_writes == 1, (
        "the persisted current manifest must be included in completed progress"
    )
    assert manifest.read_text(encoding="utf-8") == replacement, (
        "a callback failure must not undo the fact that replacement succeeded"
    )


def test_application_tracks_replacement_without_callbacks(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-replacement writer error counts without callback support."""
    _require_bounded_write_support()
    manifest = tmp_path / "Cargo.toml"
    original = '[dependencies]\nalpha = "1.0.0"\n'
    replacement = '[dependencies]\nalpha = "2.0.0"\n'
    manifest.write_text(original, encoding="utf-8")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(), original, replacement
    )
    original_write = bump_manifest_rewrite_application.write_atomic_text

    def replace_then_raise(
        path: pathlib.Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Raise after the original atomic writer commits the replacement."""
        original_write(path, content, write_options)
        raise OSError(COMPLETION_CALLBACK_FAILURE)

    monkeypatch.setattr(
        bump_manifest_rewrite_application, "write_atomic_text", replace_then_raise
    )

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteWriteError,
        match="was replaced, but a later write operation failed",
    ) as failure:
        bump_manifest_rewrite_application.apply_manifest_rewrites(
            (plan,), workspace_root=tmp_path, dry_run=False
        )

    assert failure.value.completed_writes == 1, (
        "a completed replacement must count even without write callbacks"
    )
    assert manifest.read_text(encoding="utf-8") == replacement, (
        "the post-replacement error must leave the committed replacement visible"
    )


def test_application_rejects_manifest_replaced_by_symlink_after_planning(
    tmp_path: pathlib.Path,
) -> None:
    """A symlink swapped in after planning cannot redirect an atomic write."""
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    original = '[dependencies]\nalpha = "1.0.0"\n'
    manifest.write_text(original, encoding="utf-8", newline="")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(),
        original,
        '[dependencies]\nalpha = "2.0.0"\n',
    )
    outside = tmp_path / "outside" / "Cargo.toml"
    outside.parent.mkdir()
    outside.write_text(original, encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(outside)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="no longer resolves to its planned path",
    ) as failure:
        bump_manifest_rewrites.apply_manifest_rewrites(
            (plan,), workspace_root=workspace_root, dry_run=False
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
    _require_bounded_write_support()
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
    _require_bounded_write_support()
    workspace_root = tmp_path / "workspace"
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    original = '[dependencies]\nalpha = "1.0.0"\n'
    manifest.write_text(original, encoding="utf-8", newline="")
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(),
        original,
        '[dependencies]\nalpha = "2.0.0"\n',
    )
    outside = tmp_path / "outside-Cargo.toml"
    outside.write_text(original, encoding="utf-8", newline="")
    write_text = bump_manifest_rewrite_application.write_atomic_text

    def redirect_before_write(
        file_path: pathlib.Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Swap the checked destination after the application source read."""
        file_path.unlink()
        file_path.symlink_to(outside)
        write_text(
            file_path,
            content,
            write_options,
        )

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
                (plan,), workspace_root=workspace_root, dry_run=False
            )

        assert failure.value.failed_path == plan.path, (
            "the bounded writer failure should identify the planned manifest"
        )
        assert outside.read_bytes() == original.encode("utf-8"), (
            "a redirected destination must not modify the outside file"
        )
        assert manifest.is_symlink(), "the racing symlink must not be replaced"
    finally:
        outside.unlink(missing_ok=True)


def _require_bounded_write_support() -> None:
    """Skip filesystem-write cases when safe descriptor writes are unavailable."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("workspace-bounded writes are unavailable on this platform")
