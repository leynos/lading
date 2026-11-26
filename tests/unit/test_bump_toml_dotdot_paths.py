"""Exercise safe parent-directory segments in bounded atomic writes."""

from pathlib import Path

import pytest

from lading.commands import bump_toml_files, bump_toml_write_options


def test_bounded_writer_normalizes_existing_parent_before_dot_dot(
    tmp_path: Path,
) -> None:
    """Safe existing-directory ``..`` paths resolve to their workspace target."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    (tmp_path / "fixtures").mkdir()
    destination = tmp_path / "Cargo.toml"
    destination.write_text("original", encoding="utf-8")

    bump_toml_files.write_atomic_text(
        Path("fixtures/../Cargo.toml"),
        "replacement",
        bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
    )

    assert destination.read_text(encoding="utf-8") == "replacement", (
        "bounded writes should normalize ordinary parent-directory traversal"
    )


def test_bounded_writer_rejects_symlink_hidden_before_dot_dot(
    tmp_path: Path,
) -> None:
    """A symlink traversal remains rejected even when ``..`` removes it."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("descriptor-relative replacement is unavailable on this platform")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_manifest = outside / "Cargo.toml"
    outside_manifest.write_text("outside", encoding="utf-8")
    (tmp_path / "fixtures-link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError, match="symbolic links"):
        bump_toml_files.write_atomic_text(
            Path("fixtures-link/../Cargo.toml"),
            "replacement",
            bump_toml_write_options.AtomicWriteOptions(workspace_root=tmp_path),
        )

    assert outside_manifest.read_text(encoding="utf-8") == "outside", (
        "the rejected symlink path must not write to its target"
    )
