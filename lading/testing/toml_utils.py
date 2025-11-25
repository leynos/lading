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
from tomlkit.items import Array as ArrayItem
from tomlkit.items import Table as TableItem

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
    """Load an existing TOML document or create a new one.

    Parameters
    ----------
    config_path : Path
        Filesystem path to the TOML configuration file.

    Returns
    -------
    TOMLDocument
        Parsed TOML document when the file exists; otherwise a new empty
        document.

    Raises
    ------
    TOMLKitError
        If the existing file cannot be parsed as TOML.

    """
    if config_path.exists():
        return parse_toml(config_path.read_text(encoding="utf-8"))
    return make_document()


def ensure_table(document: TOMLDocument, table_name: str) -> Table:
    """Fetch or create a top-level table in a TOML document.

    Parameters
    ----------
    document : TOMLDocument
        Document in which the table should exist.
    table_name : str
        Name of the table to fetch or create.

    Returns
    -------
    Table
        Existing table when present, or a newly created table inserted into
        ``document``.

    Raises
    ------
    AssertionError
        If an existing value at ``table_name`` is not a TOML table.

    """
    table_section = document.get(table_name)
    if table_section is None:
        table_section = table()
        document[table_name] = table_section
        return table_section

    match table_section:
        case TableItem() as existing_table:
            return existing_table
        case _:
            message = f"{table_name} must be a table"
            raise AssertionError(message)  # pragma: no cover - defensive guard


def ensure_array_field(parent_table: Table, field_name: str) -> Array:
    """Fetch or create an array field inside ``parent_table``.

    Parameters
    ----------
    parent_table : Table
        Table that should contain the array field.
    field_name : str
        Field name of the desired array.

    Returns
    -------
    Array
        Existing array when present, or a newly created array inserted into
        ``parent_table``.

    Raises
    ------
    AssertionError
        If an existing value at ``field_name`` is not an array.

    """
    raw_field = parent_table.get(field_name)
    if raw_field is None:
        field_array = array()
        parent_table[field_name] = field_array
        return field_array
    match raw_field:
        case ArrayItem() as existing_array:
            return existing_array
        case _:
            message = f"{field_name} must be an array"
            raise AssertionError(message)  # pragma: no cover - defensive guard


def append_if_absent(target_array: Array, value: str) -> None:
    """Append ``value`` to ``target_array`` if it is not already present.

    Parameters
    ----------
    target_array : Array
        TOML array to modify.
    value : str
        Value to append when missing.

    Returns
    -------
    None
        The function mutates ``target_array`` in place.

    """
    if value not in target_array:
        target_array.append(value)


def load_manifest(manifest_path: Path) -> TOMLDocument:
    """Load a TOML document from a manifest path.

    Parameters
    ----------
    manifest_path : Path
        Filesystem path to the TOML manifest to load.

    Returns
    -------
    TOMLDocument
        Parsed TOML document.

    Raises
    ------
    AssertionError
        If the manifest file does not exist.

    """
    if not manifest_path.exists():
        message = f"Manifest not found: {manifest_path}"
        raise AssertionError(message)
    return parse_toml(manifest_path.read_text(encoding="utf-8"))


def load_workspace_manifest(workspace_root: Path) -> TOMLDocument:
    """Load the workspace-level Cargo.toml manifest.

    Parameters
    ----------
    workspace_root : Path
        Root directory of the Cargo workspace.

    Returns
    -------
    TOMLDocument
        Parsed workspace manifest document.

    Raises
    ------
    AssertionError
        If the workspace Cargo.toml does not exist.

    """
    return load_manifest(workspace_root / "Cargo.toml")


def load_crate_manifest(
    workspace_root: Path, crate_name: str, *, crates_dir: str = "crates"
) -> TOMLDocument:
    """Load the Cargo.toml manifest for a specific crate.

    Parameters
    ----------
    workspace_root : Path
        Root directory of the Cargo workspace.
    crate_name : str
        Name of the crate whose manifest should be loaded.
    crates_dir : str, optional
        Subdirectory containing workspace crates (default: "crates").

    Returns
    -------
    TOMLDocument
        Parsed crate manifest document.

    Raises
    ------
    AssertionError
        If the crate manifest does not exist at the expected path.

    """
    manifest_path = workspace_root / crates_dir / crate_name / "Cargo.toml"
    return load_manifest(manifest_path)
