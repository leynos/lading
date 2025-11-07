"""Shared TOML helpers for BDD fixtures."""

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
    "load_or_create_document",
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
