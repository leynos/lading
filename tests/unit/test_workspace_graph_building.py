"""Tests for building workspace graphs from metadata."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from lading.workspace import (
    WorkspaceDependency,
    WorkspaceGraph,
    WorkspaceModelError,
    build_workspace_graph,
)
from tests.helpers.workspace_metadata import build_test_package, create_test_manifest

if typ.TYPE_CHECKING:
    from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class DependencyResolutionScenario:
    """Test scenario for workspace dependency classification cases."""

    crate_manifest: str
    dependent_manifests: tuple[tuple[str, str], ...]
    dependencies: tuple[dict[str, typ.Any], ...]
    workspace_members: tuple[str, ...]
    expected_dependencies: tuple[WorkspaceDependency, ...]


def _build_two_crate_metadata(
    workspace_root: Path,
    crate_manifest: Path,
    helper_manifest: Path,
) -> dict[str, typ.Any]:
    """Return metadata for a workspace with one crate and one helper crate."""
    return {
        "workspace_root": str(workspace_root),
        "packages": [
            build_test_package(
                "crate",
                "0.1.0",
                crate_manifest,
                dependencies=[
                    {"name": "helper", "kind": "dev"},
                    {"name": "external"},
                ],
                publish=[],
            ),
            build_test_package(
                "helper",
                "0.1.0",
                helper_manifest,
                publish=["crates-io"],
            ),
        ],
        "workspace_members": ["crate-id", "helper-id"],
    }


def test_build_workspace_graph_constructs_models(tmp_path: Path) -> None:
    """Convert metadata payloads into strongly typed workspace models."""
    workspace_root = tmp_path
    crate_manifest = create_test_manifest(
        workspace_root,
        "crate",
        """
        [package]
        name = "crate"
        version = "0.1.0"
        readme.workspace = true

        [dependencies]
        helper = { path = "../helper", version = "0.1.0" }
        """,
    )
    helper_manifest = create_test_manifest(
        workspace_root,
        "helper",
        """
        [package]
        name = "helper"
        version = "0.1.0"
        readme = "README.md"
        """,
    )
    metadata = _build_two_crate_metadata(
        workspace_root=workspace_root,
        crate_manifest=crate_manifest,
        helper_manifest=helper_manifest,
    )

    graph = build_workspace_graph(metadata)

    assert isinstance(graph, WorkspaceGraph)
    assert graph.workspace_root == workspace_root.resolve()
    crates = graph.crates_by_name
    assert set(crates) == {"crate", "helper"}
    crate = crates["crate"]
    assert crate.publish is False
    assert crate.readme_is_workspace is True
    assert crate.dependencies == (
        WorkspaceDependency(
            package_id="helper-id",
            name="helper",
            manifest_name="helper",
            kind="dev",
        ),
    ), (
        "crate.dependencies should equal expected WorkspaceDependency tuple, "
        f"got {crate.dependencies!r}"
    )
    helper = crates["helper"]
    assert helper.publish is True
    assert helper.readme_is_workspace is False
    assert helper.dependencies == (), (
        f"helper.dependencies should be empty; got {helper.dependencies!r}"
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            DependencyResolutionScenario(
                crate_manifest="""
                [package]
                name = "crate"
                version = "0.1.0"

                [dependencies]
                whitaker = { version = "0.1.0", path = "../whitaker" }
                """,
                dependent_manifests=(
                    (
                        "whitaker",
                        """
                        [package]
                        name = "whitaker"
                        version = "0.1.0"
                        """,
                    ),
                ),
                dependencies=(
                    {
                        "name": "whitaker",
                        "req": "^0.1.0",
                        "kind": None,
                        "path": "<workspace>/whitaker",
                    },
                ),
                workspace_members=("crate-id", "whitaker-id"),
                expected_dependencies=(
                    WorkspaceDependency(
                        package_id="whitaker-id",
                        name="whitaker",
                        manifest_name="whitaker",
                        kind=None,
                    ),
                ),
            ),
            id="path_version_dependency",
        ),
        pytest.param(
            DependencyResolutionScenario(
                crate_manifest="""
                [package]
                name = "crate"
                version = "0.1.0"

                [dependencies]
                alpha-core = {package = "alpha",version = "^0.1.0",path = "../alpha"}
                """,
                dependent_manifests=(
                    (
                        "alpha",
                        """
                        [package]
                        name = "alpha"
                        version = "0.1.0"
                        """,
                    ),
                ),
                dependencies=(
                    {
                        "name": "alpha",
                        "rename": "alpha-core",
                        "req": "^0.1.0",
                        "kind": None,
                    },
                ),
                workspace_members=("crate-id", "alpha-id"),
                expected_dependencies=(
                    WorkspaceDependency(
                        package_id="alpha-id",
                        name="alpha",
                        manifest_name="alpha-core",
                        kind=None,
                    ),
                ),
            ),
            id="aliased_dependency",
        ),
        pytest.param(
            DependencyResolutionScenario(
                crate_manifest="""
                [package]
                name = "crate"
                version = "0.1.0"

                [dependencies]
                serde = "1"
                """,
                dependent_manifests=(
                    (
                        "serde",
                        """
                        [package]
                        name = "serde"
                        version = "0.1.0"
                        """,
                    ),
                ),
                dependencies=(
                    {
                        "name": "serde",
                        "source": "registry+https://github.com/rust-lang/crates.io-index",
                        "req": "^1",
                        "kind": None,
                    },
                ),
                workspace_members=("crate-id", "serde-id"),
                expected_dependencies=(),
            ),
            id="ignores_registry_name_collisions",
        ),
        pytest.param(
            DependencyResolutionScenario(
                crate_manifest="""
                [package]
                name = "crate"
                version = "0.1.0"

                [dependencies]
                helper = { path = "../external/helper", version = "0.1.0" }
                """,
                dependent_manifests=(
                    (
                        "helper",
                        """
                        [package]
                        name = "helper"
                        version = "0.1.0"
                        """,
                    ),
                ),
                dependencies=(
                    {
                        "name": "helper",
                        "req": "^0.1.0",
                        "kind": None,
                        "path": "<workspace>/external/helper",
                    },
                ),
                workspace_members=("crate-id", "helper-id"),
                expected_dependencies=(),
            ),
            id="ignores_path_mismatches",
        ),
    ],
)
def test_build_workspace_graph_dependency_resolution_scenarios(
    tmp_path: Path,
    scenario: DependencyResolutionScenario,
) -> None:
    """Dependency resolution should classify internal/external entries correctly."""
    workspace_root = tmp_path
    crate_manifest = create_test_manifest(
        workspace_root,
        "crate",
        scenario.crate_manifest,
    )

    dependent_packages = []
    for crate_name, manifest_content in scenario.dependent_manifests:
        dependent_manifest = create_test_manifest(
            workspace_root,
            crate_name,
            manifest_content,
        )
        dependent_packages.append(
            build_test_package(crate_name, "0.1.0", dependent_manifest)
        )

    dependencies = tuple(
        {
            **dependency,
            "path": dependency["path"].replace("<workspace>", str(workspace_root)),
        }
        if isinstance(dependency.get("path"), str)
        else dependency
        for dependency in scenario.dependencies
    )

    metadata = {
        "workspace_root": str(workspace_root),
        "packages": [
            build_test_package(
                "crate",
                "0.1.0",
                crate_manifest,
                dependencies=list(dependencies),
            ),
            *dependent_packages,
        ],
        "workspace_members": list(scenario.workspace_members),
    }

    graph = build_workspace_graph(metadata)

    assert (
        graph.crates_by_name["crate"].dependencies == scenario.expected_dependencies
    ), (
        "graph.crates_by_name['crate'].dependencies should equal expected "
        "WorkspaceDependency tuple, "
        f"got {graph.crates_by_name['crate'].dependencies!r}"
    )


def test_build_workspace_graph_supports_package_name_fallback(tmp_path: Path) -> None:
    """Dependencies should fall back to `package` for workspace resolution."""
    workspace_root = tmp_path
    crate_manifest = create_test_manifest(
        workspace_root,
        "crate",
        """
        [package]
        name = "crate"
        version = "0.1.0"

        [dependencies]
        alpha-core = {package = "alpha",version = "^0.1.0",path = "../alpha"}
        """,
    )
    alpha_manifest = create_test_manifest(
        workspace_root,
        "alpha",
        """
        [package]
        name = "alpha"
        version = "0.1.0"
        """,
    )
    metadata = {
        "workspace_root": str(workspace_root),
        "packages": [
            build_test_package(
                "crate",
                "0.1.0",
                crate_manifest,
                dependencies=[
                    {
                        "name": "alpha-core",
                        "package": "alpha",
                        "req": "^0.1.0",
                        "kind": None,
                        "path": str(workspace_root / "alpha"),
                    }
                ],
            ),
            build_test_package(
                "alpha",
                "0.1.0",
                alpha_manifest,
            ),
        ],
        "workspace_members": ["crate-id", "alpha-id"],
    }

    graph = build_workspace_graph(metadata)

    assert graph.crates_by_name["crate"].dependencies == (
        WorkspaceDependency(
            package_id="alpha-id",
            name="alpha",
            manifest_name="alpha-core",
            kind=None,
        ),
    ), (
        "graph.crates_by_name['crate'].dependencies should equal expected "
        "WorkspaceDependency tuple, "
        f"got {graph.crates_by_name['crate'].dependencies!r}"
    )


def test_build_workspace_graph_rejects_duplicate_member_names(tmp_path: Path) -> None:
    """Duplicate workspace package names should fail index construction."""
    workspace_root = tmp_path
    shared_manifest_a = create_test_manifest(
        workspace_root,
        "shared-a",
        """
        [package]
        name = "shared"
        version = "0.1.0"
        """,
    )
    shared_manifest_b = create_test_manifest(
        workspace_root,
        "shared-b",
        """
        [package]
        name = "shared"
        version = "0.1.0"
        """,
    )
    metadata = {
        "workspace_root": str(workspace_root),
        "packages": [
            {
                "name": "shared",
                "version": "0.1.0",
                "id": "shared-a-id",
                "manifest_path": str(shared_manifest_a),
                "dependencies": [],
                "publish": None,
            },
            {
                "name": "shared",
                "version": "0.1.0",
                "id": "shared-b-id",
                "manifest_path": str(shared_manifest_b),
                "dependencies": [],
                "publish": None,
            },
        ],
        "workspace_members": ["shared-a-id", "shared-b-id"],
    }

    with pytest.raises(
        WorkspaceModelError,
        match=r"workspace package name 'shared' maps to multiple ids",
    ):
        build_workspace_graph(metadata)


def test_build_workspace_graph_rejects_missing_members(tmp_path: Path) -> None:
    """Missing package entries should surface as ``WorkspaceModelError``."""
    metadata = {
        "workspace_root": str(tmp_path),
        "packages": [],
        "workspace_members": ["crate-id"],
    }

    with pytest.raises(
        WorkspaceModelError,
        match=r"workspace member 'crate-id' missing from package list",
    ):
        build_workspace_graph(metadata)
