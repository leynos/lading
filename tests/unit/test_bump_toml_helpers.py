"""Unit tests for low-level bump TOML table and version helpers."""

from __future__ import annotations

from tomlkit import parse as parse_toml

from lading.commands import bump


def test_select_table_returns_nested_table() -> None:
    """Select nested tables using dotted selectors."""
    document = parse_toml('[workspace]\n[workspace.package]\nversion = "0.1.0"\n')
    table = bump._select_table(document, ("workspace", "package"))
    assert table is document["workspace"]["package"], (
        "dotted selector should resolve to the nested [workspace.package] table"
    )


def test_select_table_returns_none_for_missing() -> None:
    """Selectors that do not resolve to tables return ``None``."""
    document = parse_toml("[workspace]\nmembers = []\n")
    table = bump._select_table(document, ("workspace", "package"))
    assert table is None, "a selector with no matching table should return None"


def test_assign_version_handles_absent_table() -> None:
    """``_assign_version`` tolerates missing tables."""
    assert bump._assign_version(None, "1.0.0") is False, (
        "assigning a version to a missing table should be a no-op"
    )


def test_assign_version_updates_value() -> None:
    """Assign a new version when the stored value differs."""
    table = parse_toml('[package]\nname = "demo"\nversion = "0.1.0"\n')["package"]
    assert bump._assign_version(table, "2.0.0") is True, (
        "assigning a differing version should report a change"
    )
    assert table["version"] == "2.0.0", "the table version should be rewritten"


def test_assign_version_detects_existing_value() -> None:
    """Return ``False`` when the version already matches."""
    table = parse_toml('[package]\nversion = "0.1.0"\n')["package"]
    assert bump._assign_version(table, "0.1.0") is False, (
        "assigning the current version should report no change"
    )


def test_value_matches_accepts_plain_strings() -> None:
    """Strings compare directly when checking for version matches."""
    assert bump._value_matches("1.0.0", "1.0.0") is True, (
        "equal plain strings should match"
    )
    assert bump._value_matches("1.0.0", "2.0.0") is False, (
        "differing plain strings should not match"
    )


def test_value_matches_handles_toml_items() -> None:
    """TOML items compare via their stored string value."""
    document = parse_toml('version = "3.0.0"')
    item = document["version"]
    assert bump._value_matches(item, "3.0.0") is True, (
        "a TOML item should match its stored string value"
    )
    assert bump._value_matches(item, "4.0.0") is False, (
        "a TOML item should not match a different value"
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
    table = bump._select_table(document, ("package",))
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
    table = bump._select_table(document, ("package",))
    assert bump._assign_version(table, "2.0.0") is True, (
        "assigning to an out-of-order table should report a change"
    )
    assert table.get("version") == "2.0.0", (
        "out-of-order table version should be rewritten"
    )
