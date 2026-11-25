"""Unit tests for bump manifest writing and dependency-section rewrites."""

import errno
import os
import typing as typ
from pathlib import Path
from unittest import mock

import pytest
from tomlkit import parse as parse_toml

from lading import config as config_module
from lading.commands import (
    bump,
    bump_manifests,
    bump_pipeline_crate_manifests,
    bump_toml_files,
)
from lading.exceptions import LadingError
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
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


@pytest.mark.parametrize(
    ("workspace_only", "include_workspace", "expected_scope"),
    [
        (True, True, "workspace"),
        (False, True, "both"),
        (False, False, "direct"),
    ],
)
def test_dependency_section_scope_preserves_update_precedence(
    *,
    workspace_only: bool,
    include_workspace: bool,
    expected_scope: typ.Literal["direct", "workspace", "both"],
) -> None:
    """Workspace-only updates take precedence over the broad option."""
    context = bump_manifests._ManifestUpdateContext(
        options=bump.BumpOptions(include_workspace_sections=include_workspace),
        workspace_declarations_only=workspace_only,
    )

    assert bump_manifests._select_dependency_section_scope(context) == expected_scope, (
        "the dependency selector must preserve the existing scope precedence"
    )


def test_member_dependency_alias_does_not_select_excluded_workspace_package(
    tmp_path: Path,
) -> None:
    """A member alias must not become a package selector for workspace entries."""
    from tests.helpers.workspace_builders import _make_workspace

    workspace = _make_workspace(tmp_path)
    member_path = tmp_path / "crates" / "holder" / "Cargo.toml"
    member_path.parent.mkdir(parents=True)
    member_path.write_text(
        '[package]\nname = "holder"\nversion = "0.1.0"\n\n'
        "[dependencies]\n"
        'beta = { package = "alpha", version = "0.1.0" }\n\n'
        "[workspace.dependencies]\n"
        'beta-source = { package = "beta", version = "0.1.0" }\n',
        encoding="utf-8",
    )
    holder = WorkspaceCrate(
        name="holder",
        version="0.1.0",
        manifest_path=member_path,
        root_path=member_path.parent,
        publish=True,
        readme_is_workspace=False,
        dependencies=(
            WorkspaceDependency(
                package_id="alpha",
                name="alpha",
                manifest_name="beta",
            ),
        ),
    )
    workspace = WorkspaceGraph(
        workspace_root=workspace.workspace_root,
        crates=(*workspace.crates, holder),
    )
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(exclude=("beta",))
    )
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            configuration=configuration,
            workspace=workspace,
            include_workspace_sections=True,
        ),
    )

    outcome = bump_pipeline_crate_manifests._apply_crate_manifest_update(
        holder, "1.2.3", context
    )

    document = parse_toml(member_path.read_text(encoding="utf-8"))
    assert outcome is bump_pipeline_crate_manifests._CrateManifestOutcome.UPDATED, (
        "the selected member alias should still be updated"
    )
    assert document["dependencies"]["beta"]["version"] == "1.2.3", (
        "the alias key should receive the selected package's target version"
    )
    assert document["workspace"]["dependencies"]["beta-source"]["version"] == (
        "0.1.0"
    ), "the alias key must not update a workspace dependency for excluded beta"


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
    assert not list(tmp_path.glob(".lading-*")), (
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


def test_bounded_write_fails_closed_without_directory_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded writes do not fall back to an unpinned path replacement."""
    workspace_root = tmp_path / "workspace"
    manifest_path = workspace_root / "fixtures" / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        bump_toml_files,
        "_supports_bounded_directory_operations",
        lambda: False,
    )

    with pytest.raises(PermissionError, match="require descriptor-relative"):
        bump_toml_files.write_atomic_text(
            manifest_path,
            "replacement",
            workspace_root=workspace_root,
        )

    assert manifest_path.read_text(encoding="utf-8") == "original", (
        "an unsupported bounded write must leave the destination unchanged"
    )
    assert not list(manifest_path.parent.glob(".lading-*")), (
        "an unsupported bounded write must fail before creating a temporary"
    )
