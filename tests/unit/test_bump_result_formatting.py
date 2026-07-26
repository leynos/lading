"""Unit tests for ``lading.commands.bump._apply_crate_manifest_update``.

Exercises single-crate manifest updates (exclusion handling and dependency
rewrites) and hosts the workspace-construction fixtures those assertions share.
Result-message formatting lives in ``test_bump_output_formatting``.
"""

from __future__ import annotations

import dataclasses as dc
from pathlib import Path

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import _make_config


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
    """Create a test crate manifest with a single dependency."""
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
    """Create a workspace with a beta crate depending on an alpha crate."""
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
    """Return the package version and alpha dependency version from a manifest."""
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    package_version = document["package"]["version"]
    alpha_version = document["dependencies"]["alpha"].value
    return package_version, alpha_version


@pytest.mark.parametrize(
    "params",
    [
        UpdateCrateTestParams(
            test_id="excluded_crate_skips_version_bump",
            dependency_spec=("alpha", "0.1.0"),
            exclude_crates=("beta",),
            expected_package_version="0.1.0",
            expected_alpha_version="1.2.3",
        ),
        UpdateCrateTestParams(
            test_id="updates_version_and_dependencies",
            dependency_spec=("alpha", "^0.1.0"),
            exclude_crates=(),
            expected_package_version="1.2.3",
            expected_alpha_version="^1.2.3",
        ),
    ],
    ids=lambda p: p.test_id,
)
def test_update_crate_manifest(tmp_path: Path, params: UpdateCrateTestParams) -> None:
    """Crate manifest updates handle exclusions and dependency rewrites."""
    crate, workspace = _make_workspace_with_alpha_dependency(
        tmp_path,
        dependency=params.dependency_spec,
    )
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            configuration=_make_config(exclude=params.exclude_crates),
            workspace=workspace,
        ),
    )

    outcome = bump._apply_crate_manifest_update(crate, "1.2.3", context)

    assert outcome is bump._CrateManifestOutcome.UPDATED, (
        f"expected manifest update for {params.test_id}"
    )
    package_version, alpha_version = _parse_manifest_versions(crate.manifest_path)
    assert package_version == params.expected_package_version, (
        f"unexpected package version for {params.test_id}"
    )
    assert alpha_version == params.expected_alpha_version, (
        f"unexpected alpha dependency version for {params.test_id}"
    )
