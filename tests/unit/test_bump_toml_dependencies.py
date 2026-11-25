"""Test TOML dependency mutation, target selection, and warning collection."""

import pytest
from tomlkit import parse as parse_toml

from lading.commands import (
    bump_toml_dependencies,
    bump_toml_dependency_updates,
    bump_toml_dependency_warnings,
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
    """Direct aliases match package identity while workspace declarations update."""
    document = parse_toml(
        "[dependencies]\n"
        'alpha = { package = "external", version = "1.0.0" }\n\n'
        "[workspace.dependencies]\n"
        'alpha = "1.0.0"\n'
    )

    changed = bump_toml_dependency_updates.update_dependency_sections_with_warnings(
        document,
        {"dependencies": {"alpha"}},
        "2.0.0",
        section_scope="both",
    )

    assert changed, "the workspace dependency declaration should be updated"
    assert document["dependencies"]["alpha"]["version"] == "1.0.0", (
        "a local alias naming another package must keep its original requirement"
    )
    assert document["workspace"]["dependencies"]["alpha"] == "2.0.0", (
        "the matching workspace dependency declaration should receive the target"
    )
