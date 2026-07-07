"""Unit tests for bump result formatting and crate-manifest updates.

Covers change-description counting, no-change/header/path formatting, the
snapshot-backed result messages, and :func:`lading.commands.bump.
_update_crate_manifest`. Hosts the workspace-construction fixtures those
crate-update assertions share.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given
from tomlkit import parse as parse_toml

from lading.commands import bump, bump_output
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import _make_config

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


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


def test_build_changes_description_counts_sections(tmp_path: Path) -> None:
    """Descriptions enumerate manifest and documentation counts."""
    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml", tmp_path / "member" / "Cargo.toml"),
        documents=(tmp_path / "README.md",),
        transposed_readmes=(tmp_path / "crates" / "alpha" / "README.md",),
    )
    assert (
        bump_output._build_changes_description(changes)
        == "2 manifest(s), 1 documentation file(s), and 1 readme file(s)"
    ), "description should report 2 manifests, 1 documentation file, and 1 readme"

    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml",),
        documents=(tmp_path / "README.md",),
        transposed_readmes=(tmp_path / "crates" / "alpha" / "README.md",),
        lockfiles=(tmp_path / "Cargo.lock",),
    )
    assert bump_output._build_changes_description(changes) == (
        "1 manifest(s), 1 documentation file(s), 1 readme file(s), and 1 lockfile(s)"
    ), "description should report 1 manifest, 1 doc, 1 readme, and 1 lockfile"


def test_format_no_changes_message_mentions_dry_run() -> None:
    """No-change messaging adapts to dry-run context."""
    assert (
        bump_output._format_no_changes_message("1.2.3", dry_run=False)
        == "No manifest changes required; all versions already 1.2.3."
    )
    assert (
        bump_output._format_no_changes_message("1.2.3", dry_run=True)
        == "Dry run; no manifest changes required; all versions already 1.2.3."
    )


def test_format_header_labels_dry_run_requests() -> None:
    """Headers record whether the bump would be applied or was applied."""
    assert (
        bump_output._format_header("1 manifest(s)", "2.0.0", dry_run=False)
        == "Updated version to 2.0.0 in 1 manifest(s):"
    )
    assert (
        bump_output._format_header("1 manifest(s)", "2.0.0", dry_run=True)
        == "Dry run; would update version to 2.0.0 in 1 manifest(s):"
    )


def test_format_manifest_path_relative(tmp_path: Path) -> None:
    """Paths inside the workspace root are displayed relative to it."""
    workspace_root = tmp_path
    manifest_path = workspace_root / "Cargo.toml"
    manifest_path.write_text("", encoding="utf-8")
    assert (
        bump_output._format_manifest_path(manifest_path, workspace_root) == "Cargo.toml"
    )


def test_format_manifest_path_outside_workspace(tmp_path: Path) -> None:
    """Paths outside the workspace remain absolute for clarity."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = tmp_path / "external" / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("", encoding="utf-8")
    assert bump_output._format_manifest_path(manifest_path, workspace_root) == str(
        manifest_path
    )


def test_format_result_message_handles_changes(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """The formatted result message reflects manifest counts and paths."""
    workspace_root = tmp_path
    manifest_paths = [
        workspace_root / "Cargo.toml",
        workspace_root / "member" / "Cargo.toml",
    ]
    documentation_paths = [workspace_root / "README.md"]
    readme_paths = [workspace_root / "crates" / "alpha" / "README.md"]
    assert (
        bump._format_result_message(
            bump.BumpChanges(),
            "1.2.3",
            dry_run=False,
            workspace_root=workspace_root,
        )
        == "No manifest changes required; all versions already 1.2.3."
    )
    assert bump._format_result_message(
        bump.BumpChanges(manifests=manifest_paths),
        "4.5.6",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="manifests_live")
    assert bump._format_result_message(
        bump.BumpChanges(manifests=manifest_paths),
        "4.5.6",
        dry_run=True,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="manifests_dry_run")
    assert bump._format_result_message(
        bump.BumpChanges(
            manifests=manifest_paths,
            documents=documentation_paths,
        ),
        "7.8.9",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="manifests_and_docs")
    assert bump._format_result_message(
        bump.BumpChanges(
            manifests=manifest_paths,
            documents=documentation_paths,
            transposed_readmes=readme_paths,
        ),
        "7.8.9",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="all_changes")


def test_format_result_message_handles_readme_only_changes(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """README-only changes are reported as updates, not as no-ops."""
    workspace_root = tmp_path
    readme_paths = [workspace_root / "crates" / "alpha" / "README.md"]

    assert bump._format_result_message(
        bump.BumpChanges(transposed_readmes=readme_paths),
        "1.2.3",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == snapshot(name="readme_only_live")


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

    changed = bump._update_crate_manifest(
        crate,
        "1.2.3",
        context,
    )

    assert changed is True, f"expected manifest update for {params.test_id}"
    package_version, alpha_version = _parse_manifest_versions(crate.manifest_path)
    assert package_version == params.expected_package_version, (
        f"unexpected package version for {params.test_id}"
    )
    assert alpha_version == params.expected_alpha_version, (
        f"unexpected alpha dependency version for {params.test_id}"
    )


# ---------------------------------------------------------------------------
# Result-message grammar and rendering (issue #95, preserved through rebase)
# ---------------------------------------------------------------------------

_CATEGORY_COUNT = st.integers(min_value=0, max_value=3)


def _expected_description(categories: list[str]) -> str:
    """Return the reference Oxford-comma joining for ``categories``."""
    if len(categories) == 1:
        return categories[0]
    if len(categories) == 2:
        return " and ".join(categories)
    return f"{', '.join(categories[:-1])}, and {categories[-1]}"


def _build_expected_body(root: Path, changes: bump_output.BumpChanges) -> list[str]:
    """Return the expected ``"- <rel_path>"`` body lines in render order."""
    return [
        *(f"- {path.relative_to(root)}" for path in changes.manifests),
        *(f"- {path.relative_to(root)} (documentation)" for path in changes.documents),
        *(
            f"- {path.relative_to(root)} (readme)"
            for path in changes.transposed_readmes
        ),
        *(f"- {path.relative_to(root)} (lockfile)" for path in changes.lockfiles),
    ]


def _build_expected_categories(changes: bump_output.BumpChanges) -> list[str]:
    """Return ordered category descriptions for the present change sets."""
    categories: list[str] = []
    if changes.manifests:
        categories.append(f"{len(changes.manifests)} manifest(s)")
    if changes.documents:
        categories.append(f"{len(changes.documents)} documentation file(s)")
    if changes.transposed_readmes:
        categories.append(f"{len(changes.transposed_readmes)} readme file(s)")
    if changes.lockfiles:
        categories.append(f"{len(changes.lockfiles)} lockfile(s)")
    return categories


@given(
    manifest_count=_CATEGORY_COUNT,
    document_count=_CATEGORY_COUNT,
    readme_count=_CATEGORY_COUNT,
    lockfile_count=_CATEGORY_COUNT,
)
def test_result_message_grammar_and_path_rendering(
    manifest_count: int,
    document_count: int,
    readme_count: int,
    lockfile_count: int,
) -> None:
    """Any category combination renders correct grammar and unique paths."""
    root = Path("/ws")
    manifests = tuple(root / f"m{i}" / "Cargo.toml" for i in range(manifest_count))
    documents = tuple(root / f"doc{i}.md" for i in range(document_count))
    readmes = tuple(root / f"r{i}" / "README.md" for i in range(readme_count))
    lockfiles = tuple(root / f"l{i}" / "Cargo.lock" for i in range(lockfile_count))
    changes = bump_output.BumpChanges(
        manifests=manifests,
        documents=documents,
        lockfiles=lockfiles,
        transposed_readmes=readmes,
    )

    message = bump_output._format_result_message(
        changes, "1.2.3", dry_run=False, workspace_root=root
    )

    if not any((manifests, documents, readmes, lockfiles)):
        assert message == "No manifest changes required; all versions already 1.2.3."
        return

    lines = message.splitlines()
    assert lines[1:] == _build_expected_body(root, changes)

    categories = _build_expected_categories(changes)
    expected_header = (
        f"Updated version to 1.2.3 in {_expected_description(categories)}:"
    )
    assert lines[0] == expected_header
    assert " and and " not in lines[0]
    if len(categories) >= 3:
        assert ", and " in lines[0]


def test_format_result_message_four_categories_snapshot(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """All four categories render with Oxford-comma grammar (dry-run and live)."""
    root = tmp_path
    changes = bump_output.BumpChanges(
        manifests=(root / "Cargo.toml",),
        documents=(root / "README.md",),
        lockfiles=(root / "Cargo.lock",),
        transposed_readmes=(root / "crates" / "alpha" / "README.md",),
    )

    live = bump_output._format_result_message(
        changes, "2.0.0", dry_run=False, workspace_root=root
    )
    dry = bump_output._format_result_message(
        changes, "2.0.0", dry_run=True, workspace_root=root
    )

    assert snapshot(name="live") == live.splitlines()
    assert snapshot(name="dry_run") == dry.splitlines()


def test_format_result_message_lockfile_only(tmp_path: Path) -> None:
    """A lockfile-only BumpChanges renders correctly (not 'no changes')."""
    root = tmp_path
    lockfile = root / "Cargo.lock"
    changes = bump_output.BumpChanges(lockfiles=(lockfile,))
    message = bump_output._format_result_message(
        changes, "1.2.3", dry_run=False, workspace_root=root
    )
    assert message != "No manifest changes required; all versions already 1.2.3."
    lines = message.splitlines()
    assert lines[0] == "Updated version to 1.2.3 in 1 lockfile(s):"
    assert lines[1] == f"- {lockfile.relative_to(root)} (lockfile)"
