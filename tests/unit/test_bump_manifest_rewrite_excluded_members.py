"""Configured fixture rewrites honour excluded workspace members."""

from pathlib import Path

import pytest
from tomlkit import parse as parse_toml

from lading import config as config_module
from lading.commands import bump, bump_toml_files
from tests.helpers.manifest_rewrite_recovery import _make_partial_write_failure_case


def test_excluded_member_can_inherit_the_updated_workspace_version(
    tmp_path: Path,
) -> None:
    """Rewrite active member references while an excluded member inherits."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("the bounded atomic writer is unavailable")
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    fixture = case.fixtures[0]
    fixture_text = fixture.read_text(encoding="utf-8")
    fixture.write_text(
        fixture_text.replace(
            'alpha = "0.1.0"', 'alpha = "0.1.0"\nbeta = "0.1.0"'
        ).replace(
            'alpha = { path = "../alpha-0.1.0" }',
            'alpha = { path = "../alpha-0.1.0" }\nbeta = { path = "../beta-0.1.0" }',
        ),
        encoding="utf-8",
    )
    alpha_manifest.write_text(
        '[package]\nname = "alpha"\nversion.workspace = true\n',
        encoding="utf-8",
    )
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            exclude=("alpha",),
            manifest_rewrites=case.configuration.bump.manifest_rewrites,
        )
    )

    bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=configuration,
            workspace=case.workspace,
        ),
    )

    workspace_document = bump_toml_files.parse_manifest(case.root / "Cargo.toml")
    member_document = bump_toml_files.parse_manifest(alpha_manifest)
    rewritten_fixture = parse_toml(fixture.read_text(encoding="utf-8"))
    fixture_dependencies = rewritten_fixture["dependencies"]
    fixture_patch = rewritten_fixture["patch"]["crates-io"]
    assert workspace_document["workspace"]["package"]["version"] == "1.2.3", (
        "the workspace version should be bumped despite the excluded member"
    )
    assert member_document["package"]["version"]["workspace"] is True, (
        "the excluded member should continue inheriting the workspace version"
    )
    assert fixture_dependencies["beta"] == "1.2.3", (
        "the non-excluded member dependency should trigger fixture rewriting"
    )
    assert fixture_patch["beta"]["path"] == "../beta-1.2.3", (
        "the non-excluded member path should use its bumped version"
    )
    assert fixture_dependencies["alpha"] == "0.1.0", (
        "the excluded member dependency must keep its old version"
    )
    assert fixture_patch["alpha"]["path"] == "../alpha-0.1.0", (
        "the excluded member path must keep its old version"
    )
