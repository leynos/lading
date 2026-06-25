"""Unit tests for bump TOML table selection and version assignment helpers."""

from __future__ import annotations

from tomlkit import parse as parse_toml

from lading.commands import bump


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
