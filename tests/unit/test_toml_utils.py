"""Tests for ``lading.testing.toml_utils``."""

from __future__ import annotations

import typing as typ

import pytest
from tomlkit import document, table

from lading.testing import toml_utils

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_load_or_create_document_initialises_empty_document(tmp_path: Path) -> None:
    """New documents should be created when the config file is absent."""
    config_path = tmp_path / "lading.toml"

    document_obj = toml_utils.load_or_create_document(config_path)

    assert list(document_obj) == []


def test_ensure_table_rejects_non_table_values() -> None:
    """A non-table entry under the key should raise an assertion."""
    doc = document()
    doc["publish"] = "invalid"

    with pytest.raises(AssertionError, match="publish must be a table"):
        toml_utils.ensure_table(doc, "publish")


def test_ensure_array_field_rejects_non_array_values() -> None:
    """Arrays must already be TOML arrays when present."""
    parent_table = table()
    parent_table["exclude"] = "alpha"

    with pytest.raises(AssertionError, match="exclude must be an array"):
        toml_utils.ensure_array_field(parent_table, "exclude")


def test_append_if_absent_does_not_duplicate_values() -> None:
    """Appending the same value twice should only store one entry."""
    parent_table = table()
    excludes = toml_utils.ensure_array_field(parent_table, "exclude")

    toml_utils.append_if_absent(excludes, "alpha")
    toml_utils.append_if_absent(excludes, "alpha")

    assert list(excludes) == ["alpha"]


def test_load_manifest_raises_when_missing(tmp_path: Path) -> None:
    """Loading a manifest that does not exist should fail fast."""
    missing_path = tmp_path / "Cargo.toml"

    with pytest.raises(AssertionError, match="Manifest not found"):
        toml_utils.load_manifest(missing_path)


def test_load_workspace_and_crate_manifests(tmp_path: Path) -> None:
    """Helpers should resolve workspace and crate manifest paths."""
    workspace_manifest = tmp_path / "Cargo.toml"
    crate_manifest = tmp_path / "crates" / "alpha" / "Cargo.toml"
    crate_manifest.parent.mkdir(parents=True)
    workspace_manifest.write_text("[workspace]\n", encoding="utf-8")
    crate_manifest.write_text('[package]\nname = "alpha"\n', encoding="utf-8")

    workspace_doc = toml_utils.load_workspace_manifest(tmp_path)
    crate_doc = toml_utils.load_crate_manifest(tmp_path, "alpha")

    assert "workspace" in workspace_doc
    assert crate_doc["package"]["name"] == "alpha"
