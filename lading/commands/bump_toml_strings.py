r"""Update selected string values in parsed TOML tables.

This module handles direct strings or one named field in each nested table.
Value replacement and formatting preservation are delegated to
:mod:`lading.commands.bump_toml`.

Examples
--------
>>> from tomlkit import parse
>>> from lading.commands.bump_toml_strings import update_string_values
>>> document = parse('[metadata]\npath = "alpha-1.0.0"')
>>> update_string_values(
...     document["metadata"], lambda value: value.replace("1.0.0", "2.0.0")
... )
True
>>> document["metadata"]["path"]
'alpha-2.0.0'
"""

import collections.abc as cabc

from lading.commands import bump_toml
from lading.commands.bump_toml_types import (
    _TABLE_LIKE_TYPES,
    _TableLike,
)


def update_string_values(
    table: _TableLike,
    replacement: cabc.Callable[[str], str],
    *,
    field: str | None = None,
) -> bool:
    r"""Update matching strings in a table or its sub-table fields.

    Parameters
    ----------
    table : _TableLike
        TOML table whose values are inspected.
    replacement : Callable[[str], str]
        Pure transformation applied to each selected string value.
    field : str or None, optional
        When set, inspect this field in each inline or standard sub-table.
        When omitted, inspect direct string values in ``table``.

    Returns
    -------
    bool
        Whether any string value changed.

    Examples
    --------
    >>> from tomlkit import parse
    >>> document = parse('[metadata]\npath = "alpha-1.0.0"')
    >>> update_string_values(
    ...     document["metadata"], lambda value: value.replace("1.0.0", "2.0.0")
    ... )
    True
    >>> document["metadata"]["path"]
    'alpha-2.0.0'
    """
    changed = False
    for key, entry in tuple(table.items()):
        target = _selected_value_target(table, key, entry, field)
        if target is not None and _replace_value(*target, replacement):
            changed = True
    return changed


def _selected_value_target(
    table: _TableLike,
    key: str,
    entry: object,
    field: str | None,
) -> tuple[_TableLike, str, object] | None:
    """Return the container and key holding one selected string value."""
    if field is None:
        return table, key, entry
    if not isinstance(entry, _TABLE_LIKE_TYPES):
        return None
    return entry, field, entry.get(field)


def _replace_value(
    container: _TableLike,
    key: str,
    value: object,
    replacement: cabc.Callable[[str], str],
) -> bool:
    """Replace one selected string and preserve its TOML representation."""
    current = bump_toml.value_as_string(value)
    if current is None:
        return False
    replacement_item = bump_toml.prepare_string_value_replacement(
        value, replacement(current)
    )
    if replacement_item is None:
        return False
    container[key] = replacement_item  # type: ignore[index]  # Table proxies support item assignment
    return True
