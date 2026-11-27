"""Tests for style-preserving TOML string replacement.

The adapter keeps a selected value's quoting style and trailing comment when it
can. A value that the original style cannot hold falls back to a basic string
rather than corrupting the document, and a replacement identical to the source
reports no change so callers never rewrite needlessly.
"""

import pathlib
import typing as typ

import pytest
import tomlkit
from tomlkit.items import String, Table

from lading.commands import bump_manifest_rewrite_toml as toml_adapter
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.manifest_rewrites import (
    manifest_rewrite_versions,
    plan_fixture_rewrites,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _value(text: str) -> String:
    """Parse a single TOML value expression."""
    parsed = tomlkit.parse(f"value = {text}")["value"]
    assert isinstance(parsed, String), "the fixture must produce a TOML string"
    return parsed


def test_unchanged_replacement_text_returns_none() -> None:
    """A replacement equal to the current text is not a rewrite."""
    replacement = toml_adapter._prepare_string_value_replacement(
        _value('"same"'), "same"
    )

    assert replacement is None, "identical text must not produce a replacement item"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("'old'", "it's", '"it\'s"'), id="literal-apostrophe"),
        pytest.param(("'''old'''", "x'''y", "\"x'''y\""), id="multiline-delimiter"),
        pytest.param(("'old'", "a\x00b", '"a\\u0000b"'), id="literal-control-char"),
    ],
)
def test_unrepresentable_literal_falls_back_to_a_basic_string(
    case: tuple[str, str, str],
) -> None:
    """A literal style that cannot hold the text falls back to a basic string."""
    original, replacement_text, expected = case

    replacement = toml_adapter._prepare_string_value_replacement(
        _value(original), replacement_text
    )

    assert replacement is not None, "a changed value must produce a replacement"
    assert replacement.as_string() == expected, (
        "the fallback must render a basic string holding the new text"
    )


def test_multiline_style_is_kept_for_single_line_text() -> None:
    """A multiline value without a leading newline keeps its delimiters."""
    replacement = toml_adapter._prepare_string_value_replacement(
        _value('"""old"""'), "new"
    )

    assert replacement is not None, "a changed value must produce a replacement"
    assert replacement.as_string() == '"""new"""', (
        "a multiline string must keep its style when no repair is needed"
    )


def test_carriage_return_forces_a_single_line_basic_string() -> None:
    """A carriage return cannot stay in a multiline or literal string."""
    replacement = toml_adapter._prepare_string_value_replacement(
        _value("'''old'''"), "a\rb"
    )

    assert replacement is not None, "a changed value must produce a replacement"
    assert replacement.as_string() == '"a\\rb"', (
        "a carriage return must be escaped in a basic string"
    )


def test_multiline_replacement_keeps_a_leading_newline(
    snapshot: SnapshotAssertion,
) -> None:
    """A multiline value that starts with a newline keeps that newline."""
    replacement = toml_adapter._prepare_string_value_replacement(
        _value('"""old"""'), "\nfirst\nsecond"
    )

    assert replacement is not None, "a changed value must produce a replacement"
    assert replacement.as_string() == snapshot(name="multiline_leading_newline"), (
        "the repaired multiline string must round-trip the leading newline"
    )
    assert str(replacement) == "\nfirst\nsecond", (
        "the repaired string must hold the requested text exactly"
    )


def test_raw_python_values_use_default_string_flags() -> None:
    """A non-TOML value has no literal or multiline style to preserve."""
    assert toml_adapter._string_format_flags(42) == (False, False), (
        "raw values must default to a basic single-line string"
    )
    assert toml_adapter._prepare_string_value_replacement(42, "new") is None, (
        "raw values are not TOML strings and must not be replaced"
    )


def test_replace_selected_string_value_reports_unchanged_text() -> None:
    """An unchanged selection reports no write so callers can skip it."""
    document = tomlkit.parse('[metadata]\npath = "alpha-1.0.0"\n')
    table = document["metadata"]
    assert isinstance(table, Table), "the fixture needs a TOML table"

    changed = toml_adapter._replace_selected_string_value(
        table, "path", table["path"], "alpha-1.0.0"
    )

    assert changed is False, "an unchanged selection must not report a rewrite"
    assert document.as_string() == '[metadata]\npath = "alpha-1.0.0"\n', (
        "an unchanged selection must leave the document untouched"
    )


def test_planned_literal_value_keeps_its_quote_style(tmp_path: pathlib.Path) -> None:
    """A literal value that can hold the new text keeps its literal style."""
    manifest = tmp_path / "fixture/Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[metadata]\npath = 'alpha-1.0.0' # pinned\n", encoding="utf-8")

    (plan,) = plan_fixture_rewrites(
        tmp_path,
        (
            ManifestRewriteConfig(
                paths=("fixture/Cargo.toml",),
                dependencies=False,
                string_values=(StringValueRewriteConfig(table=("metadata",)),),
            ),
        ),
        manifest_rewrite_versions(),
    )

    assert plan.new_text == "[metadata]\npath = 'alpha-2.0.0' # pinned\n", (
        "the literal style and trailing comment must survive the rewrite"
    )
