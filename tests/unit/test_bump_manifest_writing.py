"""Unit tests for bump manifest writing and dependency-section rewrites."""

import errno
import os
import typing as typ
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
from lading.exceptions import LadingError
from tests.helpers.workspace_builders import _load_version

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


def test_update_manifest_writes_when_changed(tmp_path: Path) -> None:
    """Applying a new version persists changes to disk."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original, encoding="utf-8", newline="")
    result = bump_manifests._update_manifest(
        manifest_path,
        (("package",),),
        "1.0.0",
        bump_manifests._ManifestUpdateContext(bump.BumpOptions()),
    )
    assert result.was_changed is True, "changing the version should report a change"
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        written_text = handle.read()
    assert result.written_text == written_text, (
        "the result should expose the exact text written for rollback checks"
    )
    assert result.original_text == original, (
        "the result should preserve the exact source text used for its update"
    )
    assert _load_version(manifest_path, ("package",)) == "1.0.0", (
        "package version should be persisted to disk"
    )


def test_update_manifest_preserves_inline_comment(tmp_path: Path) -> None:
    """Inline comments survive manifest rewrites."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nversion = "0.1.0"  # keep me\n', encoding="utf-8"
    )
    result = bump_manifests._update_manifest(
        manifest_path,
        (("package",),),
        "1.2.3",
        bump_manifests._ManifestUpdateContext(bump.BumpOptions()),
    )
    assert result.was_changed is True, "rewriting the version should report a change"
    assert result.written_text is not None, "a persisted update should report its text"
    text = manifest_path.read_text(encoding="utf-8")
    assert "# keep me" in text, "the inline comment should survive the rewrite"
    document = parse_toml(text)
    assert document["package"]["version"] == "1.2.3", (
        "package version should be rewritten to the target"
    )


def test_update_manifest_rejects_destination_changed_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member path cannot redirect an update to another workspace manifest."""
    manifest_path = tmp_path / "member" / "Cargo.toml"
    other_manifest = tmp_path / "other" / "Cargo.toml"
    manifest_path.parent.mkdir()
    other_manifest.parent.mkdir()
    manifest_path.write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
        newline="",
    )
    other_original = b'[package]\nname = "other"\nversion = "0.5.0"\n'
    other_manifest.write_bytes(other_original)
    read_manifest = bump_manifests.bump_toml_files._read_and_parse_manifest

    def swap_alias_after_read(path: Path) -> tuple[str, TOMLDocument]:
        """Replace the selected path after its captured target is read."""
        result = read_manifest(path)
        if path == manifest_path:
            manifest_path.unlink()
            manifest_path.symlink_to(other_manifest)
        return result

    monkeypatch.setattr(
        bump_manifests.bump_toml_files,
        "_read_and_parse_manifest",
        swap_alias_after_read,
    )
    with pytest.raises(LadingError, match="destination changed after it was read"):
        bump_manifests._update_manifest(
            manifest_path,
            (("package",),),
            "1.0.0",
            bump_manifests._ManifestUpdateContext(
                options=bump.BumpOptions(),
                workspace_root=tmp_path,
            ),
        )

    assert other_manifest.read_bytes() == other_original, (
        "a path changed after reading must not update its new target"
    )
    assert manifest_path.is_symlink(), "the concurrent path replacement should remain"


def test_update_manifest_skips_when_unchanged(tmp_path: Path) -> None:
    """No write occurs when the manifest already records the target version."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original)
    result = bump_manifests._update_manifest(
        manifest_path,
        (("package",),),
        "0.1.0",
        bump_manifests._ManifestUpdateContext(bump.BumpOptions()),
    )
    assert result.was_changed is False, (
        "an already-current version should report no change"
    )
    assert result.written_text is None, (
        "an unchanged manifest should have no write text"
    )
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


def test_bounded_write_keeps_the_open_parent_when_its_path_is_swapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parent-path swap cannot redirect a bounded write through a symlink."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("directory-relative replacement is unavailable on this platform")

    workspace_root = tmp_path / "workspace"
    parent = workspace_root / "nested"
    parent.mkdir(parents=True)
    manifest = parent / "Cargo.toml"
    manifest.write_text("original", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_manifest = outside / "Cargo.toml"
    outside_manifest.write_text("outside", encoding="utf-8")
    moved_parent = workspace_root / "moved-parent"
    create_temporary_file = bump_toml_files._create_bounded_temporary_file

    def swap_parent_then_create(directory_fd: int, prefix: str) -> tuple[int, str]:
        """Replace the parent path after the writer has opened its directory."""
        parent.rename(moved_parent)
        parent.symlink_to(outside, target_is_directory=True)
        return create_temporary_file(directory_fd, prefix)

    monkeypatch.setattr(
        bump_toml_files,
        "_create_bounded_temporary_file",
        swap_parent_then_create,
    )

    bump_toml_files.write_atomic_text(
        manifest, "replacement", workspace_root=workspace_root
    )

    assert outside_manifest.read_text(encoding="utf-8") == "outside", (
        "the swapped symlink target must not receive the replacement"
    )
    assert (moved_parent / "Cargo.toml").read_text(encoding="utf-8") == (
        "replacement"
    ), "replacement must use the already-verified directory handle"
    assert parent.is_symlink(), "the concurrently installed symlink must remain"


def test_bounded_write_uses_path_fallback_without_directory_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded writes remain available when descriptor-relative calls are absent."""
    workspace_root = tmp_path / "workspace"
    manifest_path = workspace_root / "fixtures" / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )

    bump_toml_files.write_atomic_text(
        manifest_path,
        "replacement",
        workspace_root=workspace_root,
    )

    assert manifest_path.read_text(encoding="utf-8") == "replacement", (
        "the portable bounded-write fallback should replace the selected file"
    )


def test_bounded_write_revalidates_before_path_fallback_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed final containment check prevents the fallback replacement."""
    workspace_root = tmp_path / "workspace"
    manifest_path = workspace_root / "fixtures" / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )
    resolve_target = bump_toml_files._resolve_bounded_write_target
    validation_count = 0
    failure_message = "write denied"

    def reject_final_validation(path: Path, root: Path) -> Path:
        """Reject the second check, which runs immediately before replacement."""
        nonlocal validation_count
        validation_count += 1
        if validation_count == 2:
            raise PermissionError(failure_message)
        return resolve_target(path, root)

    monkeypatch.setattr(
        bump_toml_files,
        "_resolve_bounded_write_target",
        reject_final_validation,
    )

    with pytest.raises(PermissionError, match=failure_message):
        bump_toml_files.write_atomic_text(
            manifest_path,
            "replacement",
            workspace_root=workspace_root,
        )

    assert validation_count == 2, (
        "the writer must revalidate immediately before replace"
    )
    assert manifest_path.read_text(encoding="utf-8") == "original", (
        "a failed final check must leave the destination unchanged"
    )
    assert not list(manifest_path.parent.glob("Cargo.toml.*")), (
        "a failed final check must remove its temporary file"
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
