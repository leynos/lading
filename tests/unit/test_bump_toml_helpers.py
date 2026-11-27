"""Unit tests for low-level bump TOML table and version helpers."""

import os
from pathlib import Path

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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_parse_manifest_rejects_a_fifo_before_reading(tmp_path: Path) -> None:
    """A FIFO substituted for a manifest is rejected without blocking."""
    manifest = tmp_path / "Cargo.toml"
    os.mkfifo(manifest)

    with pytest.raises(OSError, match="not a regular file"):
        bump_toml.parse_manifest(manifest)


def test_replace_string_version_handles_absent_table() -> None:
    """The replace-only helper tolerates a missing table."""
    assert bump_toml.replace_string_version(None, "1.0.0") is False, (
        "replacing a version on a missing table should be a no-op"
    )


def test_replace_string_version_does_not_insert_a_missing_key() -> None:
    """A table without a ``version`` key is never given one."""
    table = parse_toml('[package]\nname = "demo"\n')["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is False, (
        "the documentation helper must not insert a missing version"
    )
    assert "version" not in table, (
        "a missing version key must stay missing after the replace-only helper"
    )


def test_replace_string_version_replaces_a_differing_string() -> None:
    """A differing string version is replaced."""
    table = parse_toml('[package]\nversion = "0.1.0"\n')["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is True, (
        "a differing string version should report a change"
    )
    assert table["version"] == "2.0.0", "the differing version should be replaced"


def test_replace_string_version_detects_an_equal_string() -> None:
    """An equal string version reports no change and keeps its trivia."""
    table = parse_toml('[package]\nversion = "2.0.0" # pinned\n')["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is False, (
        "a version already at the target should report no change"
    )
    assert "# pinned" in table.as_string(), (
        "an untouched version must keep its inline comment"
    )


def test_replace_string_version_preserves_inline_trivia() -> None:
    """Replacing a value keeps its trailing comment."""
    table = parse_toml('[package]\nversion = "0.1.0" # keep\n')["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is True, (
        "the differing version should be replaced"
    )
    assert table.as_string() == 'version = "2.0.0" # keep\n', (
        "the trailing comment and spacing must survive the replacement"
    )


def test_replace_string_version_leaves_inherited_dotted_tables_alone() -> None:
    """``version.workspace = true`` is a table, not a string to replace."""
    table = parse_toml("[package]\nversion.workspace = true\n")["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is False, (
        "a dotted inheritance table must not be replaced"
    )
    assert table.as_string() == "version.workspace = true\n", (
        "dotted inheritance must survive unchanged"
    )


def test_replace_string_version_leaves_inline_tables_alone() -> None:
    """An inline ``{ workspace = true }`` value is not a string to replace."""
    table = parse_toml("[package]\nversion = { workspace = true }\n")["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is False, (
        "an inline inheritance table must not be replaced"
    )
    assert table.as_string() == "version = { workspace = true }\n", (
        "inline inheritance must survive unchanged"
    )


def test_replace_string_version_leaves_booleans_alone() -> None:
    """A boolean version value is not a string to replace."""
    table = parse_toml("[package]\nversion = true\n")["package"]

    assert bump_toml.replace_string_version(table, "2.0.0") is False, (
        "a boolean version must not be replaced"
    )
    assert table["version"] is True, "a boolean version must keep its type"


def test_replace_string_version_works_with_out_of_order_table() -> None:
    """An ``OutOfOrderTableProxy`` is replaced through the same helper."""
    document = parse_toml(
        '[package]\nname = "x"\nversion = "0.1.0"\n'
        "[dependencies]\n"
        'foo = "1"\n'
        "[package.metadata.docs.rs]\n"
        "all-features = true\n"
    )
    table = bump_toml.select_table(document, ("package",))

    assert table is not None, "the package table must resolve"
    assert bump_toml.replace_string_version(table, "2.0.0") is True, (
        "replacing on an out-of-order table should report a change"
    )
    assert table.get("version") == "2.0.0", (
        "out-of-order table version should be replaced"
    )


def test_assign_version_still_inserts_a_missing_version() -> None:
    """The manifest helper keeps its insert-or-replace behaviour."""
    table = parse_toml('[package]\nname = "demo"\n')["package"]

    assert bump_toml.assign_version(table, "2.0.0") is True, (
        "manifest assignment must still insert a missing version"
    )
    assert table["version"] == "2.0.0", (
        "manifest assignment must write the target version"
    )
