"""Verify manifest rewrite validation for omitted package versions."""

from pathlib import Path

import msgspec

from lading.commands import bump, bump_toml
from tests.helpers.manifest_rewrite_cases import _make_partial_write_failure_case


def test_selected_member_without_version_is_assigned_target_version(
    tmp_path: Path,
) -> None:
    """A selected package with no version is bumped and passes validation."""
    case = _make_partial_write_failure_case(tmp_path)
    alpha_manifest = tmp_path / "crates/alpha/Cargo.toml"
    alpha_manifest.write_text('[package]\nname = "alpha"\n', encoding="utf-8")
    workspace = msgspec.structs.replace(
        case.workspace,
        crates=tuple(
            msgspec.structs.replace(crate, version="0.0.0")
            if crate.name == "alpha"
            else crate
            for crate in case.workspace.crates
        ),
    )
    for fixture in case.fixtures:
        text = fixture.read_text(encoding="utf-8")
        fixture.write_text(text.replace("alpha-0.1.0", "alpha-0.0.0"), encoding="utf-8")

    bump.run(
        case.root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=False,
            configuration=case.configuration,
            workspace=workspace,
        ),
    )

    member_document = bump_toml.parse_manifest(alpha_manifest)
    fixture_document = bump_toml.parse_manifest(case.fixtures[0])
    assert member_document["package"]["version"] == "1.2.3", (
        "the member updater should add the requested version field"
    )
    assert fixture_document["dependencies"]["alpha"] == "1.2.3", (
        "configured fixture dependencies should use the member's new version"
    )
    assert fixture_document["patch"]["crates-io"]["alpha"]["path"] == (
        "../alpha-1.2.3"
    ), "configured path strings should use the discovered omitted-version value"
