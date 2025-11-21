"""Unit tests for publish patch stripping behaviour."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import publish

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tomlkit.toml_document import TOMLDocument

    from lading.workspace import WorkspaceCrate


@dc.dataclass(frozen=True)
class _PatchStrategyTestSetup:
    """Parameters for patch strategy test setup."""

    tmp_path: Path
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan]
    patch_entries: str
    publishable_names: tuple[str, ...]
    strategy: str | bool


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


def _apply_strategy_and_parse(setup: _PatchStrategyTestSetup) -> TOMLDocument:
    """Set up workspace, apply patch strategy, and return parsed document."""
    workspace_root = setup.tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_text = _base_manifest(setup.patch_entries)
    _write_manifest(workspace_root, manifest_text)
    plan = setup.make_plan_factory(workspace_root, setup.publishable_names)
    publish._apply_strip_patch_strategy(workspace_root, plan, setup.strategy)
    return parse_toml((workspace_root / "Cargo.toml").read_text(encoding="utf-8"))


def test_strip_patches_all_removes_patch_section(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Strategy 'all' removes the entire [patch.crates-io] section."""
    document = _apply_strategy_and_parse(
        _PatchStrategyTestSetup(
            tmp_path=tmp_path,
            make_plan_factory=make_plan_factory,
            patch_entries='[patch.crates-io]\nalpha = { path = "crates/alpha" }\n',
            publishable_names=("alpha",),
            strategy="all",
        )
    )
    assert "patch" not in document


def test_strip_patches_per_crate_removes_publishable_only(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Strategy 'per-crate' removes only entries for publishable crates."""
    document = _apply_strategy_and_parse(
        _PatchStrategyTestSetup(
            tmp_path=tmp_path,
            make_plan_factory=make_plan_factory,
            patch_entries=(
                "[patch.crates-io]\n"
                'alpha = { path = "crates/alpha" }\n'
                'serde = { git = "https://example.com/serde" }\n'
            ),
            publishable_names=("alpha",),
            strategy="per-crate",
        )
    )
    patch_table = document.get("patch", {})
    crates_io = patch_table.get("crates-io", {})
    assert "alpha" not in crates_io
    assert "serde" in crates_io


def test_strip_patches_per_crate_removes_entire_table_when_empty(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Per-crate strategy cleans up empty patch tables after removals."""
    document = _apply_strategy_and_parse(
        _PatchStrategyTestSetup(
            tmp_path=tmp_path,
            make_plan_factory=make_plan_factory,
            patch_entries=(
                "[patch.crates-io]\n"
                'alpha = { path = "crates/alpha" }\n'
                'beta = { path = "crates/beta" }\n'
            ),
            publishable_names=("alpha", "beta"),
            strategy="per-crate",
        )
    )
    assert "patch" not in document


def test_strip_patches_disabled_keeps_section(
    tmp_path: Path,
    make_plan_factory: typ.Callable[[Path, tuple[str, ...]], publish.PublishPlan],
) -> None:
    """Boolean false leaves the patch section untouched."""
    document = _apply_strategy_and_parse(
        _PatchStrategyTestSetup(
            tmp_path=tmp_path,
            make_plan_factory=make_plan_factory,
            patch_entries='[patch.crates-io]\nalpha = { path = "crates/alpha" }\n',
            publishable_names=("alpha",),
            strategy=False,
        )
    )
    patch_table = document.get("patch", {})
    assert "crates-io" in patch_table
