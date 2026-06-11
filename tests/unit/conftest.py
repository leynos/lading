"""Shared fixtures and helpers for publish and bump unit tests.

Publish tests rely on the workspace/config factory fixtures and the
``disable_publish_preflight`` stub. Bump tests rely on
``stub_lockfile_regeneration``, which is scoped to the modules listed in
``_LOCKFILE_STUB_MODULES``.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from dataclasses import dataclass  # noqa: ICN003

import pytest
import tomlkit

from lading import config as config_module
from lading.commands import bump, publish
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph

_ORIGINAL_PREFLIGHT = publish._run_preflight_checks

# These modules drive ``bump.run`` to exercise manifest updates, documentation
# rewriting, and the rebuild_lockfiles resolution logic -- none of which need
# Cargo to actually build or regenerate lockfiles. The stub is scoped to them by
# module name so it suppresses shelling out to Cargo for these manifest- and
# resolution-focused tests while never shadowing ``test_bump_lockfiles``, which
# exercises the real ``regenerate_lockfiles`` to verify lockfile-generation
# mechanics.
_LOCKFILE_STUB_MODULES = frozenset({
    "test_bump_manifest_updates",
    "test_bump_documentation_updates",
    "test_bump_lockfile_rebuild",
    "test_bump_rebuild_lockfiles_resolution",
})

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class _CrateSpec:
    """Describe how a temporary workspace crate should be created."""

    publish: bool = True
    dependencies: tuple[WorkspaceDependency, ...] = ()
    readme_workspace: bool = False


@dataclass(frozen=True, slots=True)
class PublishFixtures:
    """Bundle reusable publish helpers to trim fixture fan-out."""

    tmp_path: Path
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate]
    make_workspace: cabc.Callable[[Path, WorkspaceCrate], WorkspaceGraph]
    make_config: cabc.Callable[..., config_module.LadingConfig]
    make_dependency: cabc.Callable[[str], WorkspaceDependency]
    publish_options: publish.PublishOptions


type PlanningFixtures = PublishFixtures
type PreparationFixtures = PublishFixtures
type PrepareWorkspaceFixtures = PublishFixtures


@pytest.fixture(autouse=True)
def disable_publish_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub publish pre-flight checks for tests that do not exercise them."""
    monkeypatch.setattr(
        publish,
        "_run_preflight_checks",
        lambda *_args, **_kwargs: None,
    )


@pytest.fixture(autouse=True)
def stub_lockfile_regeneration(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Avoid invoking Cargo from manifest-focused bump tests."""
    if request.module.__name__.rsplit(".", 1)[-1] not in _LOCKFILE_STUB_MODULES:
        return
    monkeypatch.setattr(
        bump.bump_lockfiles,
        "regenerate_lockfiles",
        lambda *_args, **_kwargs: (),
    )


@pytest.fixture
def make_config() -> cabc.Callable[..., config_module.LadingConfig]:
    """Return a factory for publish-friendly configuration objects."""

    def _make_config(
        *,
        preflight_test_exclude: tuple[str, ...] | None = None,
        preflight_unit_tests_only: bool = False,
        **overrides: object,
    ) -> config_module.LadingConfig:
        publish_table = config_module.PublishConfig(strip_patches="all", **overrides)
        preflight_config = config_module.PreflightConfig(
            test_exclude=()
            if preflight_test_exclude is None
            else preflight_test_exclude,
            unit_tests_only=preflight_unit_tests_only,
        )
        return config_module.LadingConfig(
            publish=publish_table,
            preflight=preflight_config,
        )

    return _make_config


@pytest.fixture
def make_crate() -> cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate]:
    """Return a factory that materialises temporary workspace crates."""

    def _make_crate(
        root: Path, name: str, spec: _CrateSpec | None = None
    ) -> WorkspaceCrate:
        active_spec = _CrateSpec() if spec is None else spec

        root.mkdir(parents=True, exist_ok=True)
        crate_root = root / name
        crate_root.mkdir(parents=True, exist_ok=True)
        manifest = crate_root / "Cargo.toml"

        package_table = tomlkit.table()
        package_table.add("name", name)
        package_table.add("version", "0.1.0")
        if active_spec.readme_workspace:
            readme_table = tomlkit.inline_table()
            readme_table.update({"workspace": True})
            package_table.add("readme", readme_table)

        document = tomlkit.document()
        document["package"] = package_table
        manifest.write_text(tomlkit.dumps(document), encoding="utf-8")

        return WorkspaceCrate(
            id=f"{name}-id",
            name=name,
            version="0.1.0",
            manifest_path=manifest,
            root_path=crate_root,
            publish=active_spec.publish,
            readme_is_workspace=active_spec.readme_workspace,
            dependencies=active_spec.dependencies,
        )

    return _make_crate


@pytest.fixture
def make_workspace(
    make_crate: cabc.Callable[[Path, str, _CrateSpec | None], WorkspaceCrate],
) -> cabc.Callable[[Path, WorkspaceCrate], WorkspaceGraph]:
    """Return a factory that assembles workspace graphs for tests."""

    def _make_workspace(root: Path, *crates: WorkspaceCrate) -> WorkspaceGraph:
        if not crates:
            crates = (make_crate(root, "alpha"),)
        return WorkspaceGraph(workspace_root=root, crates=tuple(crates))

    return _make_workspace


@pytest.fixture
def publish_fixtures(
    request: pytest.FixtureRequest, publish_options: publish.PublishOptions
) -> PublishFixtures:
    """Return the composite publish fixtures used across unit suites."""
    tmp_path: Path = request.getfixturevalue("tmp_path")
    make_crate = request.getfixturevalue("make_crate")
    make_workspace = request.getfixturevalue("make_workspace")
    make_config = request.getfixturevalue("make_config")
    make_dependency = request.getfixturevalue("make_dependency")
    return PublishFixtures(
        tmp_path=tmp_path,
        make_crate=make_crate,
        make_workspace=make_workspace,
        make_config=make_config,
        make_dependency=make_dependency,
        publish_options=publish_options,
    )


@pytest.fixture
def planning_fixtures(publish_fixtures: PublishFixtures) -> PlanningFixtures:
    """Expose the composite fixtures under the planning-specific alias."""
    return publish_fixtures


@pytest.fixture
def preparation_fixtures(publish_fixtures: PublishFixtures) -> PreparationFixtures:
    """Expose the composite fixtures under the staging-specific alias."""
    return publish_fixtures


@pytest.fixture
def make_dependency() -> cabc.Callable[[str], WorkspaceDependency]:
    """Return a factory for workspace dependency records."""

    def _make_dependency(name: str) -> WorkspaceDependency:
        return WorkspaceDependency(
            package_id=f"{name}-id",
            name=name,
            manifest_name=name,
            kind=None,
        )

    return _make_dependency


@pytest.fixture
def staging_root(tmp_path: Path) -> Path:
    """Provide a staging directory that sits alongside the workspace root."""
    return tmp_path.parent / f"{tmp_path.name}-staging"


@pytest.fixture
def publish_options(staging_root: Path) -> publish.PublishOptions:
    """Return publish options that stage outside the workspace root."""
    return publish.PublishOptions(build_directory=staging_root)


@pytest.fixture
def prepare_workspace_fixtures(
    publish_fixtures: PublishFixtures,
) -> PrepareWorkspaceFixtures:
    """Pre-assembled fixtures for prepare_workspace integration tests."""
    return publish_fixtures
