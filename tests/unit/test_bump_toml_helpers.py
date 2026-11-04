"""Unit tests for low-level bump TOML table and version helpers."""

import logging

import pytest
from tomlkit import parse as parse_toml

from lading.commands import bump_toml


def test_select_table_returns_nested_table() -> None:
    """Select nested tables using dotted selectors."""
    document = parse_toml('[workspace]\n[workspace.package]\nversion = "0.1.0"\n')
    table = bump_toml.select_table(document, ("workspace", "package"))
    assert table is document["workspace"]["package"], (
        "dotted selector should resolve to the nested [workspace.package] table"
    )


def test_select_table_returns_none_for_missing() -> None:
    """Selectors that do not resolve to tables return ``None``."""
    document = parse_toml("[workspace]\nmembers = []\n")
    table = bump_toml.select_table(document, ("workspace", "package"))
    assert table is None, "a selector with no matching table should return None"


def test_select_table_accepts_inline_tables() -> None:
    """Inline tables are valid targets for explicit TOML selectors."""
    document = parse_toml('dependencies = { alpha = "1.0.0" }')

    table = bump_toml.select_table(document, ("dependencies",))

    assert table is document["dependencies"], (
        "an inline table should resolve through the same selector path"
    )


def test_assign_version_handles_absent_table() -> None:
    """``assign_version`` tolerates missing tables."""
    assert bump_toml.assign_version(None, "1.0.0") is False, (
        "assigning a version to a missing table should be a no-op"
    )


def test_assign_version_updates_value() -> None:
    """Assign a new version when the stored value differs."""
    table = parse_toml('[package]\nname = "demo"\nversion = "0.1.0"\n')["package"]
    assert bump_toml.assign_version(table, "2.0.0") is True, (
        "assigning a differing version should report a change"
    )
    assert table["version"] == "2.0.0", "the table version should be rewritten"


def test_assign_version_detects_existing_value() -> None:
    """Return ``False`` when the version already matches."""
    table = parse_toml('[package]\nversion = "0.1.0"\n')["package"]
    assert bump_toml.assign_version(table, "0.1.0") is False, (
        "assigning the current version should report no change"
    )


def test_value_matches_accepts_plain_strings() -> None:
    """Strings compare directly when checking for version matches."""
    assert bump_toml.value_matches("1.0.0", "1.0.0") is True, (
        "equal plain strings should match"
    )
    assert bump_toml.value_matches("1.0.0", "2.0.0") is False, (
        "differing plain strings should not match"
    )


def test_value_matches_handles_toml_items() -> None:
    """TOML items compare via their stored string value."""
    document = parse_toml('version = "3.0.0"')
    item = document["version"]
    assert bump_toml.value_matches(item, "3.0.0") is True, (
        "a TOML item should match its stored string value"
    )
    assert bump_toml.value_matches(item, "4.0.0") is False, (
        "a TOML item should not match a different value"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('"old"', 'path = "new" # keep\n'),
        ("'old'", "path = 'new' # keep\n"),
        ('"""old"""', 'path = """new""" # keep\n'),
        ("'''old'''", "path = '''new''' # keep\n"),
    ],
    ids=["basic", "literal", "multiline-basic", "multiline-literal"],
)
def test_prepare_string_value_replacement_preserves_style_and_comment(
    source: str,
    expected: str,
) -> None:
    """Replacement retains the original TOML string delimiters and trivia."""
    document = parse_toml(f"path = {source} # keep\n")

    replacement = bump_toml.prepare_string_value_replacement(document["path"], "new")

    assert replacement is not None, "a differing string value should be replaced"
    document["path"] = replacement
    assert document.as_string() == expected, (
        "replacement should preserve string style and the trailing comment"
    )


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
    table = bump_toml.select_table(document, ("package",))
    assert table is not None, "out-of-order [package] table should still be selectable"
    assert table.get("version") == "0.1.0", (
        "selected out-of-order table should expose its version"
    )


def test_assign_version_works_with_out_of_order_table() -> None:
    """Version assignment works with OutOfOrderTableProxy."""
    document = parse_toml(
        '[package]\nname = "x"\nversion = "0.1.0"\n'
        "[dependencies]\n"
        'foo = "1"\n'
        "[package.metadata.docs.rs]\n"
        "all-features = true\n"
    )
    table = bump_toml.select_table(document, ("package",))
    assert table is not None, "the package table must resolve for this document"
    assert bump_toml.assign_version(table, "2.0.0") is True, (
        "assigning to an out-of-order table should report a change"
    )
    assert table.get("version") == "2.0.0", (
        "out-of-order table version should be rewritten"
    )


def test_prepare_version_replacement_warns_when_target_breaks_retained_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A retained comparator that excludes the target is reported."""
    with caplog.at_level(logging.WARNING, logger=bump_toml.__name__):
        replacement = bump_toml.prepare_version_replacement(">=1.0.0, <2", "2.0.0")

    assert bump_toml.value_as_string(replacement) == ">=2.0.0, <2", (
        "rewriting should preserve the retained comparator"
    )
    assert "Target version 2.0.0 does not satisfy retained Cargo bound <2" in (
        caplog.text
    ), "the impossible retained bound should produce an operator warning"


def test_prepare_version_replacement_does_not_warn_for_satisfied_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A target inside the preserved comparator range needs no warning."""
    with caplog.at_level(logging.WARNING, logger=bump_toml.__name__):
        replacement = bump_toml.prepare_version_replacement(
            ">=0.6.0-beta4, <0.7", "0.6.0"
        )

    assert bump_toml.value_as_string(replacement) == ">=0.6.0, <0.7", (
        "the first comparator should update while retaining the upper bound"
    )
    assert not caplog.records, "a satisfied retained bound should stay quiet"
