"""Unit tests for confinement of a programmatically injected workspace graph.

A caller may construct :class:`~lading.workspace.WorkspaceGraph` itself. Its
manifest paths become write targets, so they must satisfy the same contract as
a graph loaded from Cargo before any manifest is read or written.
"""

from pathlib import Path

import pytest

from lading.commands import bump
from lading.utils.confinement import BumpPreflightError, UnsafeBumpTargetError
from lading.workspace import WorkspaceCrate, WorkspaceGraph
from tests.helpers.workspace_builders import _make_config


def _crate(manifest: Path) -> WorkspaceCrate:
    """Return a minimal workspace crate whose manifest is ``manifest``."""
    return WorkspaceCrate(
        name="alpha",
        version="0.1.0",
        manifest_path=manifest,
        root_path=manifest.parent,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )


def _initialize(root: Path, graph: WorkspaceGraph) -> None:
    """Initialize a bump context for an injected graph."""
    bump._initialize_bump_context(
        root,
        bump.BumpOptions(configuration=_make_config(), workspace=graph),
    )


def test_injected_graph_rejects_a_foreign_root(tmp_path: Path) -> None:
    """An injected graph rooted elsewhere cannot be bumped from ``root``."""
    root = tmp_path.resolve()
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    graph = WorkspaceGraph(workspace_root=elsewhere, crates=())

    with pytest.raises(BumpPreflightError) as excinfo:
        _initialize(root, graph)

    assert "does not match" in str(excinfo.value), (
        "the diagnostic must say the graph root and requested root differ"
    )


def test_injected_graph_rejects_a_misnamed_member_manifest(tmp_path: Path) -> None:
    """A member manifest that is not named ``Cargo.toml`` is rejected."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    manifest = root / "crates" / "alpha" / "Other.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('version = "0.1.0"\n', encoding="utf-8")
    graph = WorkspaceGraph(workspace_root=root, crates=(_crate(manifest),))

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        _initialize(root, graph)

    assert "must be named Cargo.toml" in str(excinfo.value), (
        "the diagnostic must name the required manifest filename"
    )


def test_injected_graph_accepts_an_aliased_root_spelling(tmp_path: Path) -> None:
    """A graph that spells its root through a symlink alias still confines."""
    real = tmp_path.resolve() / "real"
    real.mkdir()
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(real, target_is_directory=True)
    (real / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    manifest = real / "crates" / "alpha" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('version = "0.1.0"\n', encoding="utf-8")
    graph = WorkspaceGraph(
        workspace_root=alias,
        crates=(_crate(alias / "crates" / "alpha" / "Cargo.toml"),),
    )

    _initialize(real, graph)
