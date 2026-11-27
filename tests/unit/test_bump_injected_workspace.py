"""Unit tests for confinement of a programmatically injected workspace graph.

A caller may construct :class:`~lading.workspace.WorkspaceGraph` itself. Its
manifest paths become write targets, so they must satisfy the same contract as
a graph loaded from Cargo before any manifest is read or written.
"""

import os
from pathlib import Path

import msgspec
import pytest

from lading.commands import bump, bump_prepare, bump_prepare_manifest_rewrites
from lading.commands.bump_manifest_rewrite_model import ManifestRewriteError
from lading.commands.bump_plan import PlanBuilder
from lading.utils.confinement import BumpPreflightError, UnsafeBumpTargetError
from lading.workspace import WorkspaceCrate, WorkspaceGraph
from lading.workspace.graph_paths import graph_relative_manifest
from tests.helpers.manifest_rewrite_cases import _make_partial_write_failure_case
from tests.helpers.workspace_builders import _make_config


def _crate(
    manifest: Path,
    *,
    readme_is_workspace: bool = False,
) -> WorkspaceCrate:
    """Return a minimal workspace crate whose manifest is ``manifest``."""
    return WorkspaceCrate(
        name="alpha",
        version="0.1.0",
        manifest_path=manifest,
        root_path=manifest.parent,
        publish=True,
        readme_is_workspace=readme_is_workspace,
        dependencies=(),
    )


def _initialize(root: Path, graph: WorkspaceGraph) -> None:
    """Initialize a bump context for an injected graph."""
    bump._initialize_bump_context(
        root,
        bump.BumpOptions(configuration=_make_config(), workspace=graph),
    )


def _rewrite_versions(root: Path, graph: WorkspaceGraph) -> dict[str, str]:
    """Return manifest-derived versions for a configured rewrite plan."""
    context = bump._initialize_bump_context(
        root,
        bump.BumpOptions(configuration=_make_config(), workspace=graph),
    )
    return bump_prepare_manifest_rewrites._pre_bump_versions_from_manifests(context)


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


def _workspace_with_member_manifest(
    tmp_path: Path,
    content: str,
    *,
    manifest_name: str = "Cargo.toml",
) -> tuple[Path, Path]:
    """Create a workspace root and one member manifest with ``content``."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    manifest = root / "crates" / "alpha" / manifest_name
    manifest.parent.mkdir(parents=True)
    manifest.write_text(content, encoding="utf-8")
    return root, manifest


def test_injected_graph_rejects_a_misnamed_member_manifest(tmp_path: Path) -> None:
    """A member manifest that is not named ``Cargo.toml`` is rejected."""
    root, manifest = _workspace_with_member_manifest(
        tmp_path, 'version = "0.1.0"\n', manifest_name="Other.toml"
    )
    graph = WorkspaceGraph(workspace_root=root, crates=(_crate(manifest),))

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        _initialize(root, graph)

    assert "must be named Cargo.toml" in str(excinfo.value), (
        "the diagnostic must name the required manifest filename"
    )


def _alias_workspace(
    tmp_path: Path,
    *,
    readme_is_workspace: bool,
) -> tuple[Path, WorkspaceGraph]:
    """Create a real workspace plus a graph that spells its root via an alias."""
    real = tmp_path.resolve() / "real"
    real.mkdir()
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(real, target_is_directory=True)
    (real / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/alpha"]\n', encoding="utf-8"
    )
    manifest = real / "crates" / "alpha" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    readme_line = "readme.workspace = true\n" if readme_is_workspace else ""
    manifest.write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n' + readme_line,
        encoding="utf-8",
    )
    graph = WorkspaceGraph(
        workspace_root=alias,
        crates=(
            _crate(
                alias / "crates" / "alpha" / "Cargo.toml",
                readme_is_workspace=readme_is_workspace,
            ),
        ),
    )
    return real, graph


def test_injected_graph_accepts_an_aliased_root_spelling(tmp_path: Path) -> None:
    """A graph that spells its root through a symlink alias still confines."""
    real, graph = _alias_workspace(tmp_path, readme_is_workspace=False)

    _initialize(real, graph)


def test_manifest_preparation_accepts_an_aliased_root_spelling(
    tmp_path: Path,
) -> None:
    """Preparation rebases an alias-spelled manifest like validation does."""
    real, graph = _alias_workspace(tmp_path, readme_is_workspace=False)
    context = bump._initialize_bump_context(
        real, bump.BumpOptions(configuration=_make_config(), workspace=graph)
    )
    builder = PlanBuilder(root=real)

    bump_prepare.prepare_manifests(context, "1.2.3", builder)

    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert "crates/alpha/Cargo.toml" in [
        proposal.path.display for proposal in plan.proposals
    ], "the alias-spelled member manifest must be prepared"


def test_readme_preparation_accepts_an_aliased_root_spelling(
    tmp_path: Path,
) -> None:
    """A README-adopting crate spelled through the alias is still prepared."""
    real, graph = _alias_workspace(tmp_path, readme_is_workspace=True)
    (real / "README.md").write_text("# Workspace README\n", encoding="utf-8")
    context = bump._initialize_bump_context(
        real, bump.BumpOptions(configuration=_make_config(), workspace=graph)
    )
    builder = PlanBuilder(root=real)

    bump_prepare.prepare_readmes(context, builder)

    plan = builder.build(target_version="1.2.3", lockfiles=None)
    assert "crates/alpha/README.md" in [
        proposal.path.display for proposal in plan.proposals
    ], "the alias-spelled crate README must be prepared beneath the real root"


def test_injected_crate_root_must_contain_its_manifest(tmp_path: Path) -> None:
    """A crate whose root_path names another directory is rejected."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    manifest = root / "crates" / "alpha" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('version = "0.1.0"\n', encoding="utf-8")
    crate = WorkspaceCrate(
        name="alpha",
        version="0.1.0",
        manifest_path=manifest,
        root_path=root / "crates" / "beta",
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )
    graph = WorkspaceGraph(workspace_root=root, crates=(crate,))

    with pytest.raises(BumpPreflightError) as excinfo:
        _initialize(root, graph)

    assert "does not contain its manifest" in str(excinfo.value), (
        "the diagnostic must say the crate root disagrees with its manifest"
    )


def test_graph_relative_manifest_rejects_parent_traversal(tmp_path: Path) -> None:
    """A manifest lexically beneath an alias may still traverse upwards."""
    root = tmp_path.resolve()
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        graph_relative_manifest(
            root, root, root / ".." / "outside" / "Cargo.toml", key="member"
        )

    assert "parent traversal" in str(excinfo.value), (
        "a parent-traversing suffix must be rejected"
    )


def test_injected_graph_rejects_a_stale_crate_version(tmp_path: Path) -> None:
    """A reused graph whose crate version drifted from disk is rejected."""
    root, manifest = _workspace_with_member_manifest(
        tmp_path, '[package]\nname = "alpha"\nversion = "2.0.0"\n'
    )
    graph = WorkspaceGraph(workspace_root=root, crates=(_crate(manifest),))

    with pytest.raises(ManifestRewriteError) as excinfo:
        _rewrite_versions(root, graph)

    assert "records crate 'alpha' at version 0.1.0" in str(excinfo.value), (
        "the diagnostic must name the graph's stale version"
    )


def test_injected_graph_rejects_a_renamed_crate(tmp_path: Path) -> None:
    """A reused graph cannot name a package the manifest no longer declares."""
    root, manifest = _workspace_with_member_manifest(
        tmp_path, '[package]\nname = "renamed-alpha"\nversion = "0.1.0"\n'
    )
    graph = WorkspaceGraph(workspace_root=root, crates=(_crate(manifest),))

    with pytest.raises(
        ManifestRewriteError, match="package name differs from workspace discovery"
    ):
        _rewrite_versions(root, graph)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("[package\n", "Could not read manifest"), id="unparseable-toml"),
        pytest.param(
            ('name = "alpha"\n', r"has no \[package\] table"),
            id="missing-package-table",
        ),
        pytest.param(
            (
                '[package]\nname = "alpha"\nversion.workspace = true\n',
                "is unavailable",
            ),
            id="inherited-version-without-workspace-value",
        ),
        pytest.param(
            ('[package]\nname = "alpha"\nversion = 1\n', "non-string package version"),
            id="non-string-version",
        ),
    ],
)
def test_rewrite_versions_require_a_usable_manifest_version(
    tmp_path: Path,
    case: tuple[str, str],
) -> None:
    """A member manifest must supply one usable version for the graph check."""
    content, expected_message = case
    root, manifest = _workspace_with_member_manifest(tmp_path, content)
    graph = WorkspaceGraph(workspace_root=root, crates=(_crate(manifest),))

    with pytest.raises(ManifestRewriteError, match=expected_message):
        _rewrite_versions(root, graph)


def test_stale_injected_graph_fails_before_any_write(tmp_path: Path) -> None:
    """A reused graph with an obsolete version is rejected before writes."""
    case = _make_partial_write_failure_case(tmp_path)
    stale_graph = msgspec.structs.replace(
        case.workspace,
        crates=tuple(
            msgspec.structs.replace(crate, version="9.9.9")
            if crate.name == "alpha"
            else crate
            for crate in case.workspace.crates
        ),
    )
    tracked = (
        case.root / "Cargo.toml",
        *(crate.manifest_path for crate in case.workspace.crates),
        *case.fixtures,
    )
    originals = {path: path.read_bytes() for path in tracked}

    with pytest.raises(ManifestRewriteError, match="declares"):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=stale_graph,
            ),
        )

    assert {path: path.read_bytes() for path in tracked} == originals, (
        "a stale graph must be rejected before any manifest or fixture is written"
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_rewrite_versions_reject_a_manifest_swapped_for_a_fifo(
    tmp_path: Path,
) -> None:
    """A member manifest swapped for a FIFO after initialization is rejected."""
    root, manifest = _workspace_with_member_manifest(
        tmp_path, '[package]\nname = "alpha"\nversion = "0.1.0"\n'
    )
    graph = WorkspaceGraph(workspace_root=root, crates=(_crate(manifest),))
    context = bump._initialize_bump_context(
        root, bump.BumpOptions(configuration=_make_config(), workspace=graph)
    )
    manifest.unlink()
    os.mkfifo(manifest)

    with pytest.raises(ManifestRewriteError, match="Could not read manifest"):
        bump_prepare_manifest_rewrites._pre_bump_versions_from_manifests(context)
