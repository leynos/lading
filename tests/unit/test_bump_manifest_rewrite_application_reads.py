"""Read planned manifest sources without blocking on special files."""

import os
import pathlib

import pytest

from lading.commands import (
    bump_manifest_rewrite_application,
    bump_manifest_rewrites,
    bump_toml_files,
)


def _plan(manifest: pathlib.Path) -> bump_manifest_rewrites.ManifestRewritePlan:
    """Return a plan whose source is the manifest's current content."""
    return bump_manifest_rewrites.ManifestRewritePlan(
        manifest.resolve(),
        manifest.read_text(encoding="utf-8"),
        "replacement",
    )


def test_application_reports_source_read_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source read failure carries its path and filesystem cause."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[dependencies]\nalpha = "1.0.0"\n', encoding="utf-8")
    plan = _plan(manifest)

    def reject_read(*_args: object, **_kwargs: object) -> tuple[str, object]:
        """Reject the bounded source read."""
        message = "read rejected"
        raise PermissionError(message)

    monkeypatch.setattr(
        bump_manifest_rewrite_application,
        "_read_bounded_regular_text",
        reject_read,
    )

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


def test_application_rejects_a_non_regular_source_without_blocking(
    tmp_path: pathlib.Path,
) -> None:
    """A FIFO source is rejected instead of blocking the workspace lock."""
    if (
        not hasattr(os, "mkfifo")
        or not bump_toml_files._supports_bounded_directory_operations()
    ):
        pytest.skip("non-blocking FIFO checks are unavailable")
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[dependencies]\nalpha = "1.0.0"\n', encoding="utf-8")
    plan = _plan(manifest)
    manifest.unlink()
    os.mkfifo(manifest)

    with pytest.raises(
        bump_manifest_rewrites.ManifestRewriteError,
        match="Could not read manifest rewrite target",
    ) as failure:
        bump_manifest_rewrites.apply_manifest_rewrites(
            (plan,), workspace_root=tmp_path, dry_run=False
        )

    assert isinstance(failure.value.__cause__, PermissionError), (
        "the non-regular destination must be rejected without blocking"
    )
