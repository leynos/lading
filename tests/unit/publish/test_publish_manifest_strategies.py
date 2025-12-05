"""Additional tests for ``lading.commands.publish_manifest``."""

from __future__ import annotations

import typing as typ
from types import SimpleNamespace

import pytest
import tomlkit

from lading.commands import publish_manifest
from lading.commands.publish_plan import PublishPlan

if typ.TYPE_CHECKING:
    from pathlib import Path


def _make_plan(workspace_root: Path, publishable_names: tuple[str, ...]) -> PublishPlan:
    """Construct a lightweight publish plan with the supplied names."""
    publishable = tuple(SimpleNamespace(name=name) for name in publishable_names)
    return PublishPlan(
        workspace_root=workspace_root,
        publishable=publishable,  # type: ignore[arg-type]
        skipped_manifest=(),
        skipped_configuration=(),
    )


def _write_manifest(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _test_strip_patch_strategy_helper(
    tmp_path: Path,
    manifest_content: str,
    publishable_names: tuple[str, ...],
    strategy: str,
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
    assert "patch" not in document


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
    assert "alpha" not in crates_io
    assert "serde" in crates_io


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

    with pytest.raises(publish_manifest.PublishPreparationError):
        publish_manifest._apply_strip_patch_strategy(tmp_path, plan, "unexpected")  # type: ignore[arg-type]


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
    assert "other" in document["patch"]["crates-io"]


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
