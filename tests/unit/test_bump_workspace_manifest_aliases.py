"""Root-manifest aliases stay in the direct dependency scope."""

from pathlib import Path

from tomlkit import parse as parse_toml

from lading import config
from lading.commands import bump, bump_pipeline_crate_manifests
from lading.workspace import WorkspaceCrate, WorkspaceDependency
from tests.helpers.workspace_builders import _make_workspace


def test_root_manifest_alias_does_not_select_excluded_workspace_package(
    tmp_path: Path,
) -> None:
    """An alias key must not match a different workspace package identity."""
    workspace = _make_workspace(tmp_path)
    root_manifest = tmp_path / "Cargo.toml"
    root_manifest.write_text(
        '[workspace]\nmembers = ["crates/alpha", "crates/beta"]\n\n'
        '[workspace.package]\nversion = "0.1.0"\n\n'
        "[workspace.dependencies]\n"
        'beta-source = { package = "beta", version = "0.1.0" }\n\n'
        '[package]\nname = "root"\nversion = "0.1.0"\n\n'
        "[dependencies]\n"
        'beta = { package = "alpha", version = "0.1.0" }\n',
        encoding="utf-8",
    )
    configuration = config.LadingConfig(bump=config.BumpConfig(exclude=("beta",)))
    context = bump._initialize_bump_context(
        tmp_path,
        bump.BumpOptions(
            configuration=configuration,
            workspace=workspace,
            include_workspace_sections=True,
        ),
    )
    root_crate = WorkspaceCrate(
        name="root",
        version="0.1.0",
        manifest_path=root_manifest,
        root_path=tmp_path,
        publish=True,
        readme_is_workspace=False,
        dependencies=(
            WorkspaceDependency(
                package_id="alpha",
                name="alpha",
                manifest_name="beta",
            ),
        ),
    )

    outcome = bump_pipeline_crate_manifests._apply_crate_manifest_update(
        root_crate, "1.2.3", context
    )

    document = parse_toml(root_manifest.read_text(encoding="utf-8"))
    assert outcome is bump_pipeline_crate_manifests._CrateManifestOutcome.UPDATED, (
        "the root direct dependency alias should be updated"
    )
    assert document["dependencies"]["beta"]["version"] == "1.2.3", (
        "the alias for selected alpha should receive the target version"
    )
    assert document["workspace"]["dependencies"]["beta-source"]["version"] == (
        "0.1.0"
    ), "the alias key must not update the excluded beta workspace package"
