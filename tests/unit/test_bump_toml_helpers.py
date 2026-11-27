"""Unit tests for low-level bump TOML table and version helpers."""

import collections.abc as cabc
import dataclasses as dc
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


# When [package.metadata.docs.rs] appears after other tables, tomlkit returns
# an OutOfOrderTableProxy instead of a Table.
_OUT_OF_ORDER_DOCUMENT = (
    '[package]\nname = "x"\nversion = "0.1.0"\n'
    "[dependencies]\n"
    'foo = "1"\n'
    "[package.metadata.docs.rs]\n"
    "all-features = true\n"
)


def test_select_table_handles_out_of_order_package() -> None:
    """Out-of-order tables (OutOfOrderTableProxy) are accepted."""
    document = parse_toml(_OUT_OF_ORDER_DOCUMENT)
    table = bump_toml.select_table(document, ("package",))
    assert table is not None, "out-of-order [package] table should still be selectable"
    assert table.get("version") == "0.1.0", (
        "selected out-of-order table should expose its version"
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


@dc.dataclass(frozen=True, slots=True)
class _ReplaceScenario:
    """One replace-only version case and its expected table rendering."""

    test_id: str
    snippet: str
    expected_changed: bool
    expected_table: str


_REPLACE_SCENARIOS = (
    _ReplaceScenario(
        test_id="missing-key",
        snippet='[package]\nname = "demo"\n',
        expected_changed=False,
        expected_table='name = "demo"\n',
    ),
    _ReplaceScenario(
        test_id="differing-string",
        snippet='[package]\nversion = "0.1.0"\n',
        expected_changed=True,
        expected_table='version = "2.0.0"\n',
    ),
    _ReplaceScenario(
        test_id="equal-string",
        snippet='[package]\nversion = "2.0.0" # pinned\n',
        expected_changed=False,
        expected_table='version = "2.0.0" # pinned\n',
    ),
    _ReplaceScenario(
        test_id="inline-comment-preserved",
        snippet='[package]\nversion = "0.1.0" # keep\n',
        expected_changed=True,
        expected_table='version = "2.0.0" # keep\n',
    ),
    _ReplaceScenario(
        test_id="inherited-dotted",
        snippet="[package]\nversion.workspace = true\n",
        expected_changed=False,
        expected_table="version.workspace = true\n",
    ),
    _ReplaceScenario(
        test_id="inherited-inline",
        snippet="[package]\nversion = { workspace = true }\n",
        expected_changed=False,
        expected_table="version = { workspace = true }\n",
    ),
    _ReplaceScenario(
        test_id="boolean-value",
        snippet="[package]\nversion = true\n",
        expected_changed=False,
        expected_table="version = true\n",
    ),
)


@pytest.mark.parametrize("scenario", _REPLACE_SCENARIOS, ids=lambda s: s.test_id)
def test_replace_string_version_only_replaces_a_differing_string(
    scenario: _ReplaceScenario,
) -> None:
    """Only an existing differing string version is replaced."""
    table = parse_toml(scenario.snippet)["package"]

    changed = bump_toml.replace_string_version(table, "2.0.0")

    assert changed is scenario.expected_changed, (
        f"{scenario.test_id}: unexpected change result for {scenario.snippet!r}"
    )
    assert table.as_string() == scenario.expected_table, (
        f"{scenario.test_id}: unexpected rendering for {scenario.snippet!r}"
    )


@pytest.mark.parametrize(
    "replace",
    [bump_toml.assign_version, bump_toml.replace_string_version],
    ids=["assign-version", "replace-string-version"],
)
def test_version_helpers_work_with_out_of_order_table(
    replace: cabc.Callable[[bump_toml.TableLike, str], bool],
) -> None:
    """Both version writers update an OutOfOrderTableProxy package table."""
    document = parse_toml(_OUT_OF_ORDER_DOCUMENT)
    table = bump_toml.select_table(document, ("package",))

    assert table is not None, "the package table must resolve for this document"
    assert replace(table, "2.0.0") is True, (
        "writing to an out-of-order table should report a change"
    )
    assert table.get("version") == "2.0.0", (
        "out-of-order table version should be rewritten"
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
