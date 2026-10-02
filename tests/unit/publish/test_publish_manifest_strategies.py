"""Additional tests for ``lading.commands.publish_manifest``."""

import typing as typ

import pytest
import tomlkit

from lading.commands import publish_manifest
from lading.commands.publish_plan import PublishPlan
from lading.workspace import WorkspaceCrate

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.config import StripPatchesSetting


def _make_plan(workspace_root: Path, publishable_names: tuple[str, ...]) -> PublishPlan:
    """Construct a publish plan whose only exercised field is the crate names.

    The strip-patch strategy under test reads nothing but the plan's crate
    names, so each crate is described purely in memory rather than
    materialised on disk. They are real
    :class:`~lading.workspace.WorkspaceCrate` instances so that the plan
    satisfies the type its consumers declare.

    Returns
    -------
    PublishPlan
        A plan that publishes one named crate per entry in
        ``publishable_names`` and skips nothing.
    """
    publishable = tuple(
        _name_only_crate(workspace_root, name) for name in publishable_names
    )
    return PublishPlan(
        workspace_root=workspace_root,
        publishable=publishable,
        skipped_manifest=(),
        skipped_configuration=(),
    )


def _name_only_crate(root: Path, name: str) -> WorkspaceCrate:
    """Return an in-memory workspace crate carrying only *name* and *root*."""
    crate_root = root / name
    return WorkspaceCrate(
        name=name,
        version="0.1.0",
        manifest_path=crate_root / "Cargo.toml",
        root_path=crate_root,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )


def _write_manifest(path: Path, body: str) -> None:
    """Write *body* to the manifest at *path*."""
    path.write_text(body, encoding="utf-8")


def _test_strip_patch_strategy_helper(
    tmp_path: Path,
    manifest_content: str,
    publishable_names: tuple[str, ...],
    strategy: StripPatchesSetting,
) -> tomlkit.TOMLDocument:
    """Write, mutate, and reload a staged manifest for strip patch checks."""
    manifest_path = tmp_path / "Cargo.toml"
    _write_manifest(manifest_path, manifest_content)
    plan = _make_plan(tmp_path, publishable_names)
    publish_manifest._apply_strip_patch_strategy(tmp_path, plan, strategy)
    return tomlkit.parse(manifest_path.read_text(encoding="utf-8"))


def test_apply_strip_patch_strategy_removes_all_entries(tmp_path: Path) -> None:
    """The 'all' strategy should drop the entire patch table."""
    document = _test_strip_patch_strategy_helper(
        tmp_path,
        """
        [patch.crates-io]
        alpha = { path = "../alpha" }
        serde = { git = "https://example.com/serde" }
        """,
        ("alpha",),
        "all",
    )
    assert "patch" not in document, (
        "the all strategy must remove the patch table entirely"
    )


def test_apply_strip_patch_strategy_removes_publishable_entries(tmp_path: Path) -> None:
    """The per-crate strategy should prune only publishable crate entries."""
    document = _test_strip_patch_strategy_helper(
        tmp_path,
        """
        [patch.crates-io]
        alpha = { path = "../alpha" }
        serde = { git = "https://example.com/serde" }
        """,
        ("alpha",),
        "per-crate",
    )
    crates_io = document["patch"]["crates-io"]
    assert "alpha" not in crates_io, (
        "the per-crate strategy must prune the publishable crate's patch entry"
    )
    assert "serde" in crates_io, (
        "the per-crate strategy must keep the third-party patch entry"
    )


def test_apply_strip_patch_strategy_skips_missing_manifest(tmp_path: Path) -> None:
    """No error should be raised when the staged manifest is absent."""
    plan = _make_plan(tmp_path, ())

    publish_manifest._apply_strip_patch_strategy(tmp_path, plan, "all")


def test_apply_strategy_to_patches_rejects_unknown_strategy(tmp_path: Path) -> None:
    """Unknown strategies should surface a clear error."""
    manifest_path = tmp_path / "Cargo.toml"
    _write_manifest(
        manifest_path,
        """
        [patch.crates-io]
        alpha = { path = "../alpha" }
        """,
    )
    plan = _make_plan(tmp_path, ("alpha",))
    # The cast is the point of the test: an unrepresentable strategy reaches
    # the helper from untyped configuration input, and must be rejected.
    unexpected = typ.cast("StripPatchesSetting", "unexpected")

    with pytest.raises(publish_manifest.PublishPreparationError):
        publish_manifest._apply_strip_patch_strategy(tmp_path, plan, unexpected)


def test_apply_strip_patch_strategy_handles_unmodified_manifest(tmp_path: Path) -> None:
    """When no matching crates exist, the manifest should be left untouched."""
    document = _test_strip_patch_strategy_helper(
        tmp_path,
        """
        [patch.crates-io]
        other = { path = "../other" }
        """,
        ("alpha",),
        "per-crate",
    )
    assert "other" in document["patch"]["crates-io"], (
        "a patch entry for a non-publishable crate must survive untouched"
    )


def test_validate_and_load_manifest_rejects_invalid_toml(tmp_path: Path) -> None:
    """Invalid manifests should surface PublishPreparationError with context."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text("[patch\n", encoding="utf-8")
    plan = _make_plan(tmp_path, ())

    with pytest.raises(publish_manifest.PublishPreparationError):
        publish_manifest._apply_strip_patch_strategy(tmp_path, plan, "all")


def test_validate_and_load_manifest_skips_non_crates_io_patch(tmp_path: Path) -> None:
    """Patch tables without crates-io entries should be ignored."""
    manifest_path = tmp_path / "Cargo.toml"
    _write_manifest(
        manifest_path,
        """
        [patch.sparse]
        serde = { git = "https://example.com/serde" }
        """,
    )
    plan = _make_plan(tmp_path, ())

    publish_manifest._apply_strip_patch_strategy(tmp_path, plan, "all")
