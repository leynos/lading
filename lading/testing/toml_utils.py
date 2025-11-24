"""Shared TOML helpers for tests.

Summary
-------
Utilities for loading, creating, and modifying TOML documents within tests
and BDD step fixtures. Centralising these helpers keeps scenarios focused on
behaviour rather than TOML plumbing.

Usage
-----
>>> from pathlib import Path
>>> from lading.testing import toml_utils
>>> doc = toml_utils.load_manifest(Path("Cargo.toml"))
>>> publish_table = toml_utils.ensure_table(doc, "publish")
>>> excludes = toml_utils.ensure_array_field(publish_table, "exclude")
>>> toml_utils.append_if_absent(excludes, "alpha")

Example manifests:
    * load_workspace_manifest(Path("/tmp/workspace"))
    * load_crate_manifest(Path("/tmp/workspace"), "alpha")
"""

from __future__ import annotations

import typing as typ

from tomlkit import array, table
from tomlkit import document as make_document
from tomlkit import parse as parse_toml

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tomlkit.items import Array, Table
    from tomlkit.toml_document import TOMLDocument

__all__ = [
    "append_if_absent",
    "ensure_array_field",
    "ensure_table",
    "load_crate_manifest",
    "load_manifest",
    "load_or_create_document",
    "load_workspace_manifest",
]


def load_or_create_document(config_path: Path) -> TOMLDocument:
    """Parse ``config_path`` if it exists, otherwise return a new document."""
    if config_path.exists():
        return parse_toml(config_path.read_text(encoding="utf-8"))
    return make_document()


def ensure_table(document: TOMLDocument, table_name: str) -> Table:
    """Fetch or create ``table_name`` within ``document``."""
    table_section = document.get(table_name)
    if table_section is None:
        table_section = table()
        document[table_name] = table_section
    return table_section


def ensure_array_field(parent_table: Table, field_name: str) -> Array:
    """Fetch or create an array field inside ``parent_table``."""
    raw_field = parent_table.get(field_name)
    if raw_field is None:
        field_array = array()
        parent_table[field_name] = field_array
        return field_array
    from tomlkit.items import Array as ArrayType

    if isinstance(raw_field, ArrayType):
        return raw_field
    message = f"{field_name} must be an array"
    raise AssertionError(message)  # pragma: no cover - defensive guard


def append_if_absent(target_array: Array, value: str) -> None:
    """Append ``value`` to ``target_array`` if it is not already present."""
    if value not in target_array:
        target_array.append(value)


def load_manifest(manifest_path: Path) -> TOMLDocument:
    """Load a TOML document from ``manifest_path`` with a helpful assertion."""
    if not manifest_path.exists():
        message = f"Manifest not found: {manifest_path}"
        raise AssertionError(message)
    return parse_toml(manifest_path.read_text(encoding="utf-8"))


def load_workspace_manifest(workspace_root: Path) -> TOMLDocument:
    """Load the workspace manifest from ``workspace_root``."""
    return load_manifest(workspace_root / "Cargo.toml")


def load_crate_manifest(
    workspace_root: Path, crate_name: str, *, crates_dir: str = "crates"
) -> TOMLDocument:
    """Load the manifest for ``crate_name`` within ``workspace_root``."""
    manifest_path = workspace_root / crates_dir / crate_name / "Cargo.toml"
    return load_manifest(manifest_path)
