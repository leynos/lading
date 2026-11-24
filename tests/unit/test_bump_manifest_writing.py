"""Unit tests for bump manifest writing and dependency-section rewrites."""

import errno
import os
from pathlib import Path
from unittest import mock

import pytest
from tomlkit import parse as parse_toml

from lading.commands import (
    bump,
    bump_manifests,
    bump_toml_dependencies,
    bump_toml_files,
)
from tests.helpers.workspace_builders import _load_version


def test_update_manifest_writes_when_changed(tmp_path: Path) -> None:
    """Applying a new version persists changes to disk."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    changed = bump_manifests._update_manifest(
        manifest_path, (("package",),), "1.0.0", bump.BumpOptions()
    )
    assert changed is True, "changing the version should report a change"
    assert _load_version(manifest_path, ("package",)) == "1.0.0", (
        "package version should be persisted to disk"
    )


def test_update_manifest_preserves_inline_comment(tmp_path: Path) -> None:
    """Inline comments survive manifest rewrites."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nversion = "0.1.0"  # keep me\n', encoding="utf-8"
    )
    changed = bump_manifests._update_manifest(
        manifest_path, (("package",),), "1.2.3", bump.BumpOptions()
    )
    assert changed is True, "rewriting the version should report a change"
    text = manifest_path.read_text(encoding="utf-8")
    assert "# keep me" in text, "the inline comment should survive the rewrite"
    document = parse_toml(text)
    assert document["package"]["version"] == "1.2.3", (
        "package version should be rewritten to the target"
    )


def test_update_manifest_skips_when_unchanged(tmp_path: Path) -> None:
    """No write occurs when the manifest already records the target version."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original)
    changed = bump_manifests._update_manifest(
        manifest_path, (("package",),), "0.1.0", bump.BumpOptions()
    )
    assert changed is False, "an already-current version should report no change"
    assert manifest_path.read_text() == original, (
        "the manifest file should be left byte-for-byte unchanged"
    )


def test_parse_manifest_preserves_crlf_when_rewritten(tmp_path: Path) -> None:
    """Parsing and writing a manifest retain its CRLF line endings."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_bytes(b'[package]\r\nname = "demo"\r\nversion = "0.1.0"\r\n')

    document = bump_toml_files.parse_manifest(manifest_path)
    document["package"]["version"] = "1.0.0"
    bump_toml_files.write_atomic_text(manifest_path, document.as_string())

    assert manifest_path.read_bytes() == (
        b'[package]\r\nname = "demo"\r\nversion = "1.0.0"\r\n'
    ), "only the version should change in a CRLF manifest"


def test_write_atomic_text_closes_descriptor_when_fchmod_fails(
    tmp_path: Path,
) -> None:
    """A failed mode copy closes the temporary fd and removes its file."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("original", encoding="utf-8")
    descriptor_numbers: list[int] = []
    failure_message = "mode change denied"

    def fail_fchmod(descriptor: int, _mode: int) -> None:
        """Capture the owned descriptor, then simulate a permission failure."""
        descriptor_numbers.append(descriptor)
        raise PermissionError(errno.EACCES, failure_message)

    with (
        mock.patch.object(bump_toml_files, "_set_file_mode", side_effect=fail_fchmod),
        pytest.raises(PermissionError, match=failure_message),
    ):
        bump_toml_files.write_atomic_text(manifest_path, "replacement")

    assert len(descriptor_numbers) == 1, "fchmod should receive the temporary fd once"
    with pytest.raises(OSError, match=r".+") as closed_descriptor:
        os.fstat(descriptor_numbers[0])
    assert closed_descriptor.value.errno == errno.EBADF, (
        "the descriptor must be closed after the permission failure"
    )
    assert manifest_path.read_text(encoding="utf-8") == "original", (
        "a failed replacement must leave the target content unchanged"
    )
    assert not list(tmp_path.glob("Cargo.toml.*")), (
        "a failed replacement must remove its temporary file"
    )


def test_write_atomic_text_preserves_symbolic_link(tmp_path: Path) -> None:
    """Atomic replacement writes through a symlink without replacing it."""
    target = tmp_path / "target.toml"
    target.write_text("original", encoding="utf-8")
    symbolic_link = tmp_path / "Cargo.toml"
    symbolic_link.symlink_to(target.name)

    bump_toml_files.write_atomic_text(symbolic_link, "replacement")

    assert symbolic_link.is_symlink(), "the configured symlink must remain intact"
    assert symbolic_link.readlink() == Path(target.name), (
        "the symbolic link must continue pointing at its original relative target"
    )
    assert target.read_text(encoding="utf-8") == "replacement", (
        "the target content must be updated through the symlink"
    )


@pytest.mark.parametrize(
    ("include_workspace_sections", "expected_workspace_alpha"),
    [(True, "^1.0.0"), (False, "^0.1.0")],
    ids=["with_workspace_flag", "without_workspace_flag"],
)
def test_update_dependency_sections_workspace_flag(
    *,
    include_workspace_sections: bool,
    expected_workspace_alpha: str,
) -> None:
    """[workspace.dependencies] updates only when the flag is set."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump_toml_dependencies.update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=include_workspace_sections,
    )
    assert changed is True, "updating [dependencies] should report a change"
    assert document["dependencies"]["alpha"].value == "1.0.0", (
        "[dependencies] alpha should always be rewritten"
    )
    assert (
        document["workspace"]["dependencies"]["alpha"].value == expected_workspace_alpha
    ), "[workspace.dependencies] alpha should update only with the workspace flag"


def test_update_dependency_sections_workspace_only() -> None:
    """When only workspace sections exist, they are updated with the flag."""
    document = parse_toml('[workspace.dependencies]\nalpha = "0.1.0"\n')
    changed = bump_toml_dependencies.update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "2.0.0",
        include_workspace_sections=True,
    )
    assert changed is True, "updating a workspace-only section should report a change"
    assert document["workspace"]["dependencies"]["alpha"].value == "2.0.0", (
        "[workspace.dependencies] alpha should be rewritten to the target"
    )


def test_update_dependency_sections_workspace_dev_and_build() -> None:
    """Workspace dev-dependencies and build-dependencies are updated."""
    document = parse_toml(
        '[workspace.dev-dependencies]\nalpha = "~0.1.0"\n\n'
        '[workspace.build-dependencies]\nbeta = { version = "0.1.0" }\n'
    )
    changed = bump_toml_dependencies.update_dependency_sections(
        document,
        {"dev-dependencies": ("alpha",), "build-dependencies": ("beta",)},
        "3.0.0",
        include_workspace_sections=True,
    )
    assert changed is True, "updating dev/build sections should report a change"
    assert document["workspace"]["dev-dependencies"]["alpha"].value == "~3.0.0", (
        "[workspace.dev-dependencies] alpha should be rewritten"
    )
    build_deps = document["workspace"]["build-dependencies"]
    assert build_deps["beta"]["version"].value == "3.0.0", (
        "[workspace.build-dependencies] beta.version should be rewritten"
    )
