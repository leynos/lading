"""Internal unit tests for the :mod:`lading.commands.bump` module internals."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import (
    _load_version,
    _make_config,
    _make_workspace,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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

    Returns:
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

    Returns:
        Tuple of (package_version, alpha_dependency_version).

    """
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    package_version = document["package"]["version"]
    alpha_version = document["dependencies"]["alpha"].value
    return package_version, alpha_version


def test_validate_bump_options_requires_configuration_and_workspace() -> None:
    """Validation raises when mandatory options are missing."""
    with pytest.raises(ValueError, match="must supply configuration and workspace"):
        bump._validate_bump_options(bump.BumpOptions())


def test_validate_bump_options_returns_configuration_and_workspace(
    tmp_path: Path,
) -> None:
    """Validation succeeds when both configuration and workspace are present."""
    configuration = _make_config()
    workspace = _make_workspace(tmp_path)
    options = bump.BumpOptions(configuration=configuration, workspace=workspace)
    assert bump._validate_bump_options(options) == (configuration, workspace)


def test_determine_package_selectors_respects_exclusions() -> None:
    """Excluded crates produce no package selectors."""
    assert bump._determine_package_selectors("beta", {"beta"}) == ()


def test_determine_package_selectors_includes_package_for_active_crates() -> None:
    """Active crates receive the package selector tuple."""
    assert bump._determine_package_selectors("beta", set()) == (("package",),)


def test_should_skip_crate_update_requires_selectors_or_dependencies() -> None:
    """Skipping occurs only when both selectors and dependency sections are empty."""
    assert bump._should_skip_crate_update((), {}) is True
    assert (
        bump._should_skip_crate_update((("package",),), {"dependencies": ("alpha",)})
        is False
    )


def test_build_changes_description_counts_sections(tmp_path: Path) -> None:
    """Descriptions enumerate manifest and documentation counts."""
    changes = bump.BumpChanges(
        manifests=(tmp_path / "Cargo.toml", tmp_path / "member" / "Cargo.toml"),
        documents=(tmp_path / "README.md",),
    )
    assert (
        bump._build_changes_description(changes)
        == "2 manifest(s) and 1 documentation file(s)"
    )


def test_format_no_changes_message_mentions_dry_run() -> None:
    """No-change messaging adapts to dry-run context."""
    assert (
        bump._format_no_changes_message("1.2.3", dry_run=False)
        == "No manifest changes required; all versions already 1.2.3."
    )
    assert (
        bump._format_no_changes_message("1.2.3", dry_run=True)
        == "Dry run; no manifest changes required; all versions already 1.2.3."
    )


def test_format_header_labels_dry_run_requests() -> None:
    """Headers record whether the bump would be applied or was applied."""
    assert (
        bump._format_header("1 manifest(s)", "2.0.0", dry_run=False)
        == "Updated version to 2.0.0 in 1 manifest(s):"
    )
    assert (
        bump._format_header("1 manifest(s)", "2.0.0", dry_run=True)
        == "Dry run; would update version to 2.0.0 in 1 manifest(s):"
    )


def test_format_manifest_path_relative(tmp_path: Path) -> None:
    """Paths inside the workspace root are displayed relative to it."""
    workspace_root = tmp_path
    manifest_path = workspace_root / "Cargo.toml"
    manifest_path.write_text("", encoding="utf-8")
    assert bump._format_manifest_path(manifest_path, workspace_root) == "Cargo.toml"


def test_format_manifest_path_outside_workspace(tmp_path: Path) -> None:
    """Paths outside the workspace remain absolute for clarity."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest_path = tmp_path / "external" / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("", encoding="utf-8")
    assert bump._format_manifest_path(manifest_path, workspace_root) == str(
        manifest_path
    )


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
    options = bump.BumpOptions(
        configuration=_make_config(exclude=params.exclude_crates),
        workspace=workspace,
    )

    changed = bump._update_crate_manifest(
        crate,
        "1.2.3",
        options,
    )

    assert changed is True
    package_version, alpha_version = _parse_manifest_versions(crate.manifest_path)
    assert package_version == params.expected_package_version
    assert alpha_version == params.expected_alpha_version


def test_format_result_message_handles_changes(tmp_path: Path) -> None:
    """The formatted result message reflects manifest counts and paths."""
    workspace_root = tmp_path
    manifest_paths = [
        workspace_root / "Cargo.toml",
        workspace_root / "member" / "Cargo.toml",
    ]
    documentation_paths = [workspace_root / "README.md"]
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
    ).splitlines() == [
        "Updated version to 4.5.6 in 2 manifest(s):",
        "- Cargo.toml",
        "- member/Cargo.toml",
    ]
    assert bump._format_result_message(
        bump.BumpChanges(manifests=manifest_paths),
        "4.5.6",
        dry_run=True,
        workspace_root=workspace_root,
    ).splitlines() == [
        "Dry run; would update version to 4.5.6 in 2 manifest(s):",
        "- Cargo.toml",
        "- member/Cargo.toml",
    ]
    assert bump._format_result_message(
        bump.BumpChanges(manifests=manifest_paths, documents=documentation_paths),
        "7.8.9",
        dry_run=False,
        workspace_root=workspace_root,
    ).splitlines() == [
        "Updated version to 7.8.9 in 2 manifest(s) and 1 documentation file(s):",
        "- Cargo.toml",
        "- member/Cargo.toml",
        "- README.md (documentation)",
    ]


def test_update_manifest_writes_when_changed(tmp_path: Path) -> None:
    """Applying a new version persists changes to disk."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    changed = bump._update_manifest(
        manifest_path, (("package",),), "1.0.0", bump.BumpOptions()
    )
    assert changed is True
    assert _load_version(manifest_path, ("package",)) == "1.0.0"


def test_update_manifest_preserves_inline_comment(tmp_path: Path) -> None:
    """Inline comments survive manifest rewrites."""
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        '[package]\nversion = "0.1.0"  # keep me\n', encoding="utf-8"
    )
    changed = bump._update_manifest(
        manifest_path, (("package",),), "1.2.3", bump.BumpOptions()
    )
    assert changed is True
    text = manifest_path.read_text(encoding="utf-8")
    assert "# keep me" in text
    document = parse_toml(text)
    assert document["package"]["version"] == "1.2.3"


def test_update_manifest_skips_when_unchanged(tmp_path: Path) -> None:
    """No write occurs when the manifest already records the target version."""
    manifest_path = tmp_path / "Cargo.toml"
    original = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    manifest_path.write_text(original)
    changed = bump._update_manifest(
        manifest_path, (("package",),), "0.1.0", bump.BumpOptions()
    )
    assert changed is False
    assert manifest_path.read_text() == original


def test_select_table_returns_nested_table() -> None:
    """Select nested tables using dotted selectors."""
    document = parse_toml('[workspace]\n[workspace.package]\nversion = "0.1.0"\n')
    table = bump._select_table(document, ("workspace", "package"))
    assert table is document["workspace"]["package"]


def test_select_table_returns_none_for_missing() -> None:
    """Selectors that do not resolve to tables return ``None``."""
    document = parse_toml("[workspace]\nmembers = []\n")
    table = bump._select_table(document, ("workspace", "package"))
    assert table is None


def test_assign_version_handles_absent_table() -> None:
    """``_assign_version`` tolerates missing tables."""
    assert bump._assign_version(None, "1.0.0") is False


def test_assign_version_updates_value() -> None:
    """Assign a new version when the stored value differs."""
    table = parse_toml('[package]\nname = "demo"\nversion = "0.1.0"\n')["package"]
    assert bump._assign_version(table, "2.0.0") is True
    assert table["version"] == "2.0.0"


def test_assign_version_detects_existing_value() -> None:
    """Return ``False`` when the version already matches."""
    table = parse_toml('[package]\nversion = "0.1.0"\n')["package"]
    assert bump._assign_version(table, "0.1.0") is False


def test_value_matches_accepts_plain_strings() -> None:
    """Strings compare directly when checking for version matches."""
    assert bump._value_matches("1.0.0", "1.0.0") is True
    assert bump._value_matches("1.0.0", "2.0.0") is False


def test_value_matches_handles_toml_items() -> None:
    """TOML items compare via their stored string value."""
    document = parse_toml('version = "3.0.0"')
    item = document["version"]
    assert bump._value_matches(item, "3.0.0") is True
    assert bump._value_matches(item, "4.0.0") is False


def test_select_table_handles_out_of_order_package() -> None:
    """Out-of-order tables (OutOfOrderTableProxy) are accepted."""
    # When [package.metadata.docs.rs] appears after other tables,
    # tomlkit returns an OutOfOrderTableProxy instead of a Table
    document = parse_toml(
        '[package]\nname = "x"\nversion = "0.1.0"\n'
        "[dependencies]\n"
        'foo = "1"\n'
        "[package.metadata.docs.rs]\n"
        "all-features = true\n"
    )
    table = bump._select_table(document, ("package",))
    assert table is not None
    assert table.get("version") == "0.1.0"


def test_assign_version_works_with_out_of_order_table() -> None:
    """Version assignment works with OutOfOrderTableProxy."""
    document = parse_toml(
        '[package]\nname = "x"\nversion = "0.1.0"\n'
        "[dependencies]\n"
        'foo = "1"\n'
        "[package.metadata.docs.rs]\n"
        "all-features = true\n"
    )
    table = bump._select_table(document, ("package",))
    assert bump._assign_version(table, "2.0.0") is True
    assert table.get("version") == "2.0.0"
