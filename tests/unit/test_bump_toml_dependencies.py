"""Test TOML dependency mutation, target selection, and warning collection."""

import typing as typ

import pytest
from tomlkit import parse as parse_toml

from lading.commands import (
    bump_toml_dependencies,
    bump_toml_dependency_updates,
    bump_toml_dependency_warnings,
)


@pytest.mark.parametrize(
    ("include_workspace_sections", "expected_workspace_alpha"),
    [(True, "^1.0.0"), (False, "^0.1.0")],
    ids=["with_workspace_flag", "without_workspace_flag"],
)
def test_update_dependency_sections_workspace_flag(
    *,
    include_workspace_sections: bool,
    expected_workspace_alpha: str,
) -> None:
    """[workspace.dependencies] updates only when the flag is set."""
    document = parse_toml(
        '[dependencies]\nalpha = "0.1.0"\n\n'
        '[workspace.dependencies]\nalpha = "^0.1.0"\n'
    )
    changed = bump_toml_dependencies.update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "1.0.0",
        include_workspace_sections=include_workspace_sections,
    )
    assert changed is True, "updating [dependencies] should report a change"
    assert document["dependencies"]["alpha"].value == "1.0.0", (
        "[dependencies] alpha should always be rewritten"
    )
    assert (
        document["workspace"]["dependencies"]["alpha"].value == expected_workspace_alpha
    ), "[workspace.dependencies] alpha should update only with the workspace flag"


def test_update_dependency_sections_workspace_only() -> None:
    """When only workspace sections exist, they are updated with the flag."""
    document = parse_toml('[workspace.dependencies]\nalpha = "0.1.0"\n')
    changed = bump_toml_dependencies.update_dependency_sections(
        document,
        {"dependencies": ("alpha",)},
        "2.0.0",
        include_workspace_sections=True,
    )
    assert changed is True, "updating a workspace-only section should report a change"
    assert document["workspace"]["dependencies"]["alpha"].value == "2.0.0", (
        "[workspace.dependencies] alpha should update to the target"
    )


def test_update_dependency_sections_workspace_dev_and_build() -> None:
    """Workspace dev-dependencies and build-dependencies are updated."""
    document = parse_toml(
        '[workspace.dev-dependencies]\nalpha = "~0.1.0"\n\n'
        '[workspace.build-dependencies]\nbeta = { version = "0.1.0" }\n'
    )
    changed = bump_toml_dependencies.update_dependency_sections(
        document,
        {"dev-dependencies": ("alpha",), "build-dependencies": ("beta",)},
        "3.0.0",
        include_workspace_sections=True,
    )
    assert changed is True, "updating dev/build sections should report a change"
    assert document["workspace"]["dev-dependencies"]["alpha"].value == "~3.0.0", (
        "[workspace.dev-dependencies] alpha should be rewritten"
    )
    build_deps = document["workspace"]["build-dependencies"]
    assert build_deps["beta"]["version"].value == "3.0.0", (
        "[workspace.build-dependencies] beta.version should be rewritten"
    )


@pytest.mark.parametrize(
    "case",
    [
        (">=1.0.0, <2", "2.0.0", ("<2",), ">=2.0.0, <2"),
        ("<0.7", "0.6.0", ("<0.6.0",), "<0.6.0"),
        (">0.6.0-beta4", "0.6.0", (">0.6.0",), ">0.6.0"),
    ],
)
def test_dependency_update_warns_when_bound_excludes_target(
    caplog: pytest.LogCaptureFixture,
    case: tuple[str, str, tuple[str, ...], str],
) -> None:
    """The helper returns warning data without logging as a side effect."""
    requirement, target, expected_bounds, expected_requirement = case
    document = parse_toml(f'[dependencies]\nalpha = "{requirement}"\n')
    warnings = bump_toml_dependency_warnings.collect_dependency_section_warnings(
        document, {"dependencies": {"alpha"}}, target
    )

    changed = bump_toml_dependencies.update_dependency_sections(
        document, {"dependencies": {"alpha"}}, target
    )

    assert changed, "the selected dependency should be rewritten"
    assert document["dependencies"]["alpha"] == expected_requirement, (
        "dependency mutation should retain the first comparator and later bounds"
    )
    assert len(warnings) == 1, "the collector should return one warning record"
    assert warnings[0].excluded_bounds == expected_bounds, (
        "returned warning data should identify the comparator that excludes target"
    )
    assert not caplog.records, "TOML mutation helpers must not log warnings"


def test_dependency_update_does_not_warn_when_target_satisfies_bounds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A valid target retains additional Cargo bounds without a warning."""
    document = parse_toml('[dependencies]\nalpha = ">=0.6.0-beta4, <0.7"\n')
    warnings = bump_toml_dependency_warnings.collect_dependency_section_warnings(
        document, {"dependencies": {"alpha"}}, "0.6.0"
    )

    changed = bump_toml_dependencies.update_dependency_sections(
        document, {"dependencies": {"alpha"}}, "0.6.0"
    )

    assert changed, "the selected dependency should be rewritten"
    assert document["dependencies"]["alpha"] == ">=0.6.0, <0.7", (
        "the rewritten lower comparator should retain its satisfied upper bound"
    )
    assert not warnings, "a satisfied upper bound should produce no warning data"
    assert not caplog.records, "TOML mutation helpers must not log warnings"


@pytest.mark.parametrize(
    ("name", "entry", "expected"),
    [
        ("alpha", '"1.0.0"', "alpha"),
        ("renamed", '{ package = "alpha", version = "1.0.0" }', "alpha"),
        ("alpha", '{ path = "../alpha" }', "alpha"),
        ("alpha", "{ workspace = true }", None),
        ("alpha", "false", None),
    ],
)
def test_dependency_target_name_uses_package_alias_and_table_keys(
    name: str,
    entry: str,
    expected: str | None,
) -> None:
    """TOML dependency shape determines the package, not its alias alone."""
    document = parse_toml(f"entry = {entry}")

    assert (
        bump_toml_dependencies.dependency_target_name(name, document["entry"])
        == expected
    ), "dependency identity should follow its package field or key"


def test_workspace_update_does_not_rewrite_local_alias_for_another_package() -> None:
    """Workspace declarations match package identity rather than alias keys."""
    document = parse_toml(
        "[dependencies]\n"
        'alpha = { package = "external", version = "1.0.0" }\n\n'
        "[workspace.dependencies]\n"
        'alpha-core = { package = "alpha", version = "1.0.0" }\n'
    )

    changed = bump_toml_dependency_updates.update_dependency_sections_with_warnings(
        document,
        {"dependencies": {"alpha"}},
        "2.0.0",
        section_scope="workspace",
    )

    assert changed, "the workspace dependency declaration should be updated"
    assert document["dependencies"]["alpha"]["version"] == "1.0.0", (
        "a local alias naming another package must keep its original requirement"
    )
    assert document["workspace"]["dependencies"]["alpha-core"]["version"] == (
        "2.0.0"
    ), "workspace aliases should match the selected crate by package identity"


def test_direct_dependency_update_uses_the_selected_manifest_key() -> None:
    """A selected dependency alias updates even when its package name differs."""
    document = parse_toml(
        '[dependencies]\nalpha-core = { package = "alpha", version = "1.0.0" }\n'
    )

    changed = bump_toml_dependency_updates.update_dependency_sections_with_warnings(
        document,
        {"dependencies": {"alpha-core"}},
        "2.0.0",
        section_scope="direct",
    )

    assert changed, "the selected alias should be updated"
    assert document["dependencies"]["alpha-core"]["version"] == "2.0.0", (
        "direct dependency sections should match the manifest's alias key"
    )


@pytest.mark.parametrize("section_scope", ["direct", "both"])
def test_direct_key_does_not_select_another_package_alias(
    section_scope: typ.Literal["direct", "both"],
) -> None:
    """A selected key must not widen selection to another package alias."""
    document = parse_toml(
        "[dependencies]\n"
        'alpha = { package = "beta", version = "1.0.0" }\n'
        'renamed = { package = "alpha", version = "1.0.0" }\n'
    )

    changed = bump_toml_dependency_updates.update_dependency_sections_with_warnings(
        document,
        {"dependencies": {"alpha"}},
        "2.0.0",
        section_scope=section_scope,
    )

    assert changed, "the selected direct manifest key should be updated"
    assert document["dependencies"]["alpha"]["version"] == "2.0.0", (
        "the selected manifest key should receive the target version"
    )
    assert document["dependencies"]["renamed"]["version"] == "1.0.0", (
        "a different alias must not be selected by its package identity"
    )


def test_both_scope_matches_a_direct_alias_by_package_name() -> None:
    """Direct and workspace aliases both follow the selected package identity."""
    document = parse_toml(
        "[dependencies]\n"
        'local-alpha = { package = "alpha", version = ">=1.0.0, <2" }\n\n'
        "[workspace.dependencies]\n"
        'alpha-core = { package = "alpha", version = ">=1.0.0, <2" }\n'
    )

    changed = bump_toml_dependency_updates.update_dependency_sections_with_warnings(
        document,
        {"dependencies": {"alpha"}},
        "1.5.0",
        section_scope="both",
    )

    assert changed, "both selected sections should update package aliases"
    assert document["dependencies"]["local-alpha"]["version"] == ">=1.5.0, <2", (
        "direct aliases should match by their package field"
    )
    assert document["workspace"]["dependencies"]["alpha-core"]["version"] == (
        ">=1.5.0, <2"
    ), "workspace aliases should retain the same package requirement"


def test_both_scope_matches_a_direct_alias_by_manifest_key() -> None:
    """Combined updates retain direct-key matching for member selections."""
    document = parse_toml(
        '[dependencies]\nrenamed = { package = "alpha", version = "1.0.0" }\n'
    )

    changed = bump_toml_dependency_updates.update_dependency_sections_with_warnings(
        document,
        {"dependencies": {"renamed"}},
        "2.0.0",
        section_scope="both",
    )

    assert changed, "combined scope should update the selected direct alias key"
    assert document["dependencies"]["renamed"]["version"] == "2.0.0", (
        "the manifest key remains a valid direct-section selector in both scope"
    )
