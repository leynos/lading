"""Workspace and manifest builders for bump command internal tests."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import string
from pathlib import Path

import hypothesis.strategies as st
from tomlkit import parse as parse_toml

from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph


@dc.dataclass(frozen=True, slots=True)
class UpdateCrateTestParams:
    """Parameters for test_update_crate_manifest parametrized tests."""

    test_id: str
    dependency_spec: tuple[str, str]
    exclude_crates: tuple[str, ...]
    expected_package_version: str
    expected_alpha_version: str


def _make_test_crate_with_dependency(
    tmp_path: Path,
    *,
    crate_name: str = "beta",
    crate_version: str = "0.1.0",
    dependency: tuple[str, str] = ("alpha", "0.1.0"),
) -> WorkspaceCrate:
    """Create a test crate manifest with a single dependency.

    Args:
        tmp_path: Directory where the manifest will be created.
        crate_name: Name of the crate to create.
        crate_version: Version of the crate.
        dependency: Tuple of (dependency_name, dependency_version).

    """
    dependency_name, dependency_version = dependency
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        f"""
        [package]
        name = "{crate_name}"
        version = "{crate_version}"

        [dependencies]
        {dependency_name} = "{dependency_version}"
        """,
        encoding="utf-8",
    )
    return WorkspaceCrate(
        id=f"{crate_name}-id",
        name=crate_name,
        version=crate_version,
        manifest_path=manifest_path,
        root_path=manifest_path.parent,
        publish=True,
        readme_is_workspace=False,
        dependencies=(
            WorkspaceDependency(
                package_id=f"{dependency_name}-id",
                name=dependency_name,
                manifest_name=dependency_name,
                kind=None,
            ),
        ),
    )


def _make_workspace_with_alpha_dependency(
    tmp_path: Path,
    *,
    dependency: tuple[str, str] = ("alpha", "0.1.0"),
) -> tuple[WorkspaceCrate, WorkspaceGraph]:
    """Create a workspace with beta crate depending on alpha crate.

    Args:
        tmp_path: Directory where manifests will be created.
        dependency: Tuple of (dependency_name, dependency_version) for beta's
            dependency.

    Returns
    -------
        Tuple of (beta_crate, workspace_graph).

    """
    beta_crate = _make_test_crate_with_dependency(tmp_path, dependency=dependency)

    alpha_manifest = tmp_path / "alpha" / "Cargo.toml"
    alpha_manifest.parent.mkdir(parents=True, exist_ok=True)
    alpha_manifest.write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    alpha_crate = WorkspaceCrate(
        id="alpha-id",
        name="alpha",
        version="0.1.0",
        manifest_path=alpha_manifest,
        root_path=alpha_manifest.parent,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )

    workspace = WorkspaceGraph(
        workspace_root=tmp_path,
        crates=(beta_crate, alpha_crate),
    )

    return beta_crate, workspace


def _parse_manifest_versions(
    manifest_path: Path,
) -> tuple[str, str]:
    """Extract package version and alpha dependency version from manifest.

    Args:
        manifest_path: Path to the Cargo.toml manifest.

    Returns
    -------
        Tuple of (package_version, alpha_dependency_version).

    """
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    package_version = document["package"]["version"]
    alpha_version = document["dependencies"]["alpha"].value
    return package_version, alpha_version


_crate_name = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-_",
    min_size=1,
    max_size=12,
)


def _synthetic_workspace(names: cabc.Iterable[str]) -> WorkspaceGraph:
    """Build an in-memory workspace graph for the supplied crate names."""
    root = Path("/ws")
    crates = tuple(
        WorkspaceCrate(
            id=f"{name}-id",
            name=name,
            version="0.1.0",
            manifest_path=root / name / "Cargo.toml",
            root_path=root / name,
            publish=True,
            readme_is_workspace=False,
            dependencies=(),
        )
        for name in names
    )
    return WorkspaceGraph(workspace_root=root, crates=crates)
