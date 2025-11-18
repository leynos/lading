"""Unit tests for publish patch stripping behaviour."""

from __future__ import annotations

import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import publish

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.workspace import WorkspaceCrate


@pytest.fixture
def make_plan_factory(
    make_crate: typ.Callable[[Path, str, object | None], WorkspaceCrate],
) -> typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan]:
    """Return a factory for building publish plans rooted at ``workspace_root``."""

    def _builder(
        workspace_root: Path, publishable_names: tuple[str, ...]
    ) -> publish.PublishPlan:
        crates: list[WorkspaceCrate] = []
        for name in publishable_names:
            crate = make_crate(workspace_root / "crates", name)
            crates.append(crate)
        return publish.PublishPlan(
            workspace_root=workspace_root,
            publishable=tuple(crates),
            skipped_manifest=(),
            skipped_configuration=(),
            missing_configuration_exclusions=(),
        )

    return _builder


def _write_manifest(workspace_root: Path, body: str) -> Path:
    manifest = workspace_root / "Cargo.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(body, encoding="utf-8")
    return manifest


def _base_manifest(entries: str = "") -> str:
    return (
        "[workspace]\n"
        'members = ["crates/alpha"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n\n'
        f"{entries}"
    )


def test_strip_patches_all_removes_patch_section(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Strategy 'all' removes the entire [patch.crates-io] section."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_text = _base_manifest(
        '[patch.crates-io]\nalpha = { path = "crates/alpha" }\n'
    )
    _write_manifest(workspace_root, manifest_text)
    plan = make_plan_factory(workspace_root, ("alpha",))

    publish._apply_strip_patch_strategy(workspace_root, plan, "all")

    document = parse_toml((workspace_root / "Cargo.toml").read_text(encoding="utf-8"))
    assert "patch" not in document


def test_strip_patches_per_crate_removes_publishable_only(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Strategy 'per-crate' removes only entries for publishable crates."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_text = _base_manifest(
        "[patch.crates-io]\n"
        'alpha = { path = "crates/alpha" }\n'
        'serde = { git = "https://example.com/serde" }\n'
    )
    _write_manifest(workspace_root, manifest_text)
    plan = make_plan_factory(workspace_root, ("alpha",))

    publish._apply_strip_patch_strategy(workspace_root, plan, "per-crate")

    document = parse_toml((workspace_root / "Cargo.toml").read_text(encoding="utf-8"))
    patch_table = document.get("patch", {})
    crates_io = patch_table.get("crates-io", {})
    assert "alpha" not in crates_io
    assert "serde" in crates_io


def test_strip_patches_per_crate_removes_entire_table_when_empty(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Per-crate strategy cleans up empty patch tables after removals."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_text = _base_manifest(
        "[patch.crates-io]\n"
        'alpha = { path = "crates/alpha" }\n'
        'beta = { path = "crates/beta" }\n'
    )
    _write_manifest(workspace_root, manifest_text)
    plan = make_plan_factory(workspace_root, ("alpha", "beta"))

    publish._apply_strip_patch_strategy(workspace_root, plan, "per-crate")

    document = parse_toml((workspace_root / "Cargo.toml").read_text(encoding="utf-8"))
    assert "patch" not in document


def test_strip_patches_disabled_keeps_section(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Boolean false leaves the patch section untouched."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_text = _base_manifest(
        '[patch.crates-io]\nalpha = { path = "crates/alpha" }\n'
    )
    _write_manifest(workspace_root, manifest_text)
    plan = make_plan_factory(workspace_root, ("alpha",))

    publish._apply_strip_patch_strategy(workspace_root, plan, strategy=False)

    document = parse_toml((workspace_root / "Cargo.toml").read_text(encoding="utf-8"))
    patch_table = document.get("patch", {})
    assert "crates-io" in patch_table
