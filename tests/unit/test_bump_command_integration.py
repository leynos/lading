"""Integration-focused unit tests for the :mod:`lading.commands.bump` module."""

from __future__ import annotations

import dataclasses as dc
import pathlib
import typing as typ

import pytest
from tomlkit import items as tk_items
from tomlkit import parse as parse_toml

from lading import config as config_module
from lading.commands import bump
from lading.workspace import WorkspaceDependency, WorkspaceGraph
from tests.helpers.workspace_builders import (
    _build_workspace_with_internal_deps,
    _CrateSpec,
    _create_alpha_crate,
    _create_beta_crate_with_dependencies,
    _load_version,
    _make_config,
    _make_workspace,
    _write_workspace_manifest,
)

if typ.TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


@dc.dataclass(frozen=True, slots=True)
class _NoChangeScenario:
    """Parameters describing expected output when no manifests change."""

    test_id: str
    dry_run: bool
    expected_message: str


def _extract_alpha_dependency_entries(
    manifest_path: pathlib.Path,
) -> tuple[str, object, object]:
    """Return the alpha dependency entries across manifest sections."""
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    dependency = document["dependencies"]["alpha"].value
    dev_entry = document["dev-dependencies"]["alpha"]
    build_entry = document["build-dependencies"]["alpha"]
    return dependency, dev_entry, build_entry


def test_run_updates_workspace_and_members(tmp_path: pathlib.Path) -> None:
    """`bump.run` updates the workspace and member manifest versions."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    options = bump.BumpOptions(configuration=configuration, workspace=workspace)
    message = bump.run(tmp_path, "1.2.3", options=options)
    assert message.splitlines() == [
        "Updated version to 1.2.3 in 3 manifest(s):",
        "- Cargo.toml",
        "- crates/alpha/Cargo.toml",
        "- crates/beta/Cargo.toml",
    ]
    assert _load_version(tmp_path / "Cargo.toml", ("workspace", "package")) == "1.2.3"
    for crate in workspace.crates:
        assert _load_version(crate.manifest_path, ("package",)) == "1.2.3"


def test_run_updates_root_package_section(tmp_path: pathlib.Path) -> None:
    """The workspace manifest `[package]` section also receives the new version."""
    workspace = _make_workspace(tmp_path)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[package]\n"
        'name = "workspace"\n'
        'version = "0.1.0"\n\n'
        "[workspace]\n"
        'members = ["crates/alpha", "crates/beta"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n'
    )
    configuration = _make_config()
    bump.run(
        tmp_path,
        "7.8.9",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    assert _load_version(manifest_path, ("package",)) == "7.8.9"
    assert _load_version(manifest_path, ("workspace", "package")) == "7.8.9"


def test_run_skips_excluded_crates(tmp_path: pathlib.Path) -> None:
    """Crates listed in `bump.exclude` retain their original version."""
    workspace = _make_workspace(tmp_path)
    excluded = workspace.crates[0]
    configuration = _make_config(exclude=(excluded.name,))
    bump.run(
        tmp_path,
        "2.0.0",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    assert _load_version(tmp_path / "Cargo.toml", ("workspace", "package")) == "2.0.0"
    assert _load_version(excluded.manifest_path, ("package",)) == "0.1.0"
    included = workspace.crates[1]
    assert _load_version(included.manifest_path, ("package",)) == "2.0.0"


def test_run_updates_internal_dependency_versions(tmp_path: pathlib.Path) -> None:
    """Internal dependency requirements are updated across dependency sections."""
    alpha_crate = _create_alpha_crate(tmp_path)
    beta_crate = _create_beta_crate_with_dependencies(tmp_path, alpha_crate.id)
    _write_workspace_manifest(
        tmp_path,
        [
            "crates/alpha",
            "crates/beta",
        ],
    )
    workspace = WorkspaceGraph(
        workspace_root=tmp_path, crates=(alpha_crate, beta_crate)
    )

    configuration = _make_config()
    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    dependency_version, dev_entry, build_entry = _extract_alpha_dependency_entries(
        beta_crate.manifest_path
    )
    assert dependency_version == "^1.2.3"
    assert dev_entry["version"].value == "~1.2.3"
    assert dev_entry["path"].value == "../alpha"
    assert build_entry["version"].value == "1.2.3"
    assert build_entry["path"].value == "../alpha"


def test_run_updates_documentation_snippets(tmp_path: pathlib.Path) -> None:
    """Documentation TOML fences are rewritten to reference the new version."""
    workspace = _make_workspace(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        """# Sample\n\n```toml\n[dependencies]\nalpha = \"0.1.0\"\n```\n""",
        encoding="utf-8",
    )
    configuration = _make_config(documentation_globs=("README.md",))

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    assert "documentation file(s)" in message
    assert "- README.md (documentation)" in message.splitlines()
    updated_readme = readme_path.read_text(encoding="utf-8")
    assert 'alpha = "1.2.3"' in updated_readme


def test_run_updates_renamed_internal_dependency_versions(
    tmp_path: pathlib.Path,
) -> None:
    """Aliased workspace dependencies are updated using their manifest name."""
    workspace, manifests = _build_workspace_with_internal_deps(
        tmp_path,
        specs=(
            _CrateSpec(name="alpha"),
            _CrateSpec(
                name="beta",
                manifest_extra="""
                [dependencies]
                alpha-core = { package = "alpha", version = "^0.1.0" }
                """,
                dependencies=(
                    WorkspaceDependency(
                        package_id="alpha-id",
                        name="alpha",
                        manifest_name="alpha-core",
                        kind=None,
                    ),
                ),
            ),
        ),
    )

    configuration = _make_config()
    bump.run(
        tmp_path,
        "2.3.4",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    beta_manifest = manifests["beta"]
    beta_document = parse_toml(beta_manifest.read_text(encoding="utf-8"))
    dependency_entry = beta_document["dependencies"]["alpha-core"]
    assert dependency_entry["version"].value == "^2.3.4"
    assert dependency_entry["package"].value == "alpha"


def test_run_normalises_workspace_root(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """The command resolves the workspace root before applying updates."""
    workspace_root = tmp_path / "workspace-root"
    workspace = _make_workspace(workspace_root)
    configuration = _make_config()
    relative = pathlib.Path("workspace-root")
    monkeypatch.chdir(tmp_path)
    bump.run(
        relative,
        "3.4.5",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )
    manifest_path = workspace_root / "Cargo.toml"
    assert _load_version(manifest_path, ("workspace", "package")) == "3.4.5"


def test_run_uses_loaded_configuration_and_workspace(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """`bump.run` loads the configuration and workspace when omitted."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    monkeypatch.setattr(config_module, "current_configuration", lambda: configuration)
    monkeypatch.setattr("lading.workspace.load_workspace", lambda root: workspace)
    bump.run(tmp_path, "9.9.9")
    assert _load_version(tmp_path / "Cargo.toml", ("workspace", "package")) == "9.9.9"


@pytest.mark.parametrize(
    "scenario",
    [
        _NoChangeScenario(
            test_id="live",
            dry_run=False,
            expected_message=(
                "No manifest changes required; all versions already 0.1.0."
            ),
        ),
        _NoChangeScenario(
            test_id="dry-run",
            dry_run=True,
            expected_message=(
                "Dry run; no manifest changes required; all versions already 0.1.0."
            ),
        ),
    ],
    ids=lambda scenario: scenario.test_id,
)
def test_run_reports_when_versions_already_match(
    tmp_path: pathlib.Path, scenario: _NoChangeScenario
) -> None:
    """Report the no-op message for both live and dry-run invocations."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    message = bump.run(
        tmp_path,
        "0.1.0",
        options=bump.BumpOptions(
            dry_run=scenario.dry_run,
            configuration=configuration,
            workspace=workspace,
        ),
    )
    assert message == scenario.expected_message


def test_run_dry_run_reports_changes_without_modifying_files(
    tmp_path: pathlib.Path,
) -> None:
    """Dry-running the command reports planned changes without touching manifests."""
    workspace = _make_workspace(tmp_path)
    configuration = _make_config()
    manifest_paths = [
        tmp_path / "Cargo.toml",
        *[crate.manifest_path for crate in workspace.crates],
    ]
    original_contents = {
        path: path.read_text(encoding="utf-8") for path in manifest_paths
    }

    message = bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True, configuration=configuration, workspace=workspace
        ),
    )

    assert message.splitlines() == [
        "Dry run; would update version to 1.2.3 in 3 manifest(s):",
        "- Cargo.toml",
        "- crates/alpha/Cargo.toml",
        "- crates/beta/Cargo.toml",
    ]
    for path in manifest_paths:
        assert path.read_text(encoding="utf-8") == original_contents[path]

@pytest.mark.parametrize(
    ("section", "versions"),
    [
        ("dependencies", ('"0.1.0"', "1.2.3", "1.2.3")),
        ("dev-dependencies", ('"~0.1.0"', "2.0.0", "~2.0.0")),
        ("build-dependencies", ('{ version = "0.1.0" }', "3.0.0", "3.0.0")),
    ],
    ids=["dependencies", "dev-dependencies", "build-dependencies"],
)
def test_run_updates_workspace_dependency_sections(
    tmp_path: pathlib.Path,
    section: str,
    versions: tuple[str, str, str],
) -> None:
    """Workspace dependency entries in [workspace.<section>] are updated."""
    version_spec, target_version, expected_version = versions
    workspace = _make_workspace(tmp_path)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[workspace]\n"
        'members = ["crates/alpha", "crates/beta"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n\n'
        f"[workspace.{section}]\n"
        f"alpha = {version_spec}\n",
        encoding="utf-8",
    )
    configuration = _make_config()
    bump.run(
        tmp_path,
        target_version,
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    entry = document["workspace"][section]["alpha"]
    # Handle both string format ("0.1.0") and table format ({ version = "0.1.0" })
    if isinstance(entry, (tk_items.Table, tk_items.InlineTable)):
        actual_version = entry["version"].value
    else:
        actual_version = entry.value
    assert actual_version == expected_version


def test_run_updates_workspace_dependency_prefixes(tmp_path: pathlib.Path) -> None:
    """Workspace dependency requirements preserve prefixes and extra fields."""
    workspace = _make_workspace(tmp_path)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[workspace]\n"
        'members = ["crates/alpha", "crates/beta"]\n\n'
        "[workspace.package]\n"
        'version = "0.1.0"\n\n'
        "[workspace.dependencies]\n"
        'alpha = "^0.1.0"\n'
        'beta = { version = "~0.1.0", path = "crates/beta" }\n',
        encoding="utf-8",
    )
    configuration = _make_config()
    bump.run(
        tmp_path,
        "1.2.3",
        options=bump.BumpOptions(configuration=configuration, workspace=workspace),
    )

    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    assert document["workspace"]["dependencies"]["alpha"].value == "^1.2.3"
    beta_entry = document["workspace"]["dependencies"]["beta"]
    assert beta_entry["version"].value == "~1.2.3"
    assert beta_entry["path"].value == "crates/beta"
