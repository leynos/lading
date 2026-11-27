"""Preserve and replace TOML string values selected for rewriting.

This adapter owns table selection and style-preserving string assignment. The
matching module supplies the replacement callable and remains responsible for
template semantics and SemVer protection.
"""

import collections.abc as cabc
import typing as typ
from contextlib import suppress

import tomlkit
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import InvalidStringError
from tomlkit.items import InlineTable as _InlineTable
from tomlkit.items import Item as _Item
from tomlkit.items import String as _String
from tomlkit.items import Table as _Table

from lading.commands import bump_toml

type _TableLike = _Table | _InlineTable | OutOfOrderTableProxy
_TABLE_TYPES: typ.Final[
    tuple[
        type[_Table] | type[_InlineTable] | type[OutOfOrderTableProxy],
        ...,
    ]
] = (
    _Table,
    _InlineTable,
    OutOfOrderTableProxy,
)


def _prepare_string_value_replacement(
    value: object,
    replacement_text: str,
) -> _Item | None:
    """Return string content with its TOML style and trivia preserved.

    Parameters
    ----------
    value : object
        The current TOML value, either a tomlkit :class:`~_Item`
        or a raw Python value.
    replacement_text : str
        The complete string value to store.

    Returns
    -------
    Item | None
        The replacement item, or ``None`` when ``value`` is not a string or
        already contains ``replacement_text``.

    Raises
    ------
    InvalidStringError
        If the replacement cannot be represented as a TOML basic string.

    Examples
    --------
    >>> from tomlkit import parse
    >>> document = parse("path = 'old' # keep")
    >>> document["path"] = _prepare_string_value_replacement(
    ...     document["path"], "new"
    ... )
    >>> document.as_string()
    "path = 'new' # keep"
    >>> _prepare_string_value_replacement(42, "new") is None
    True
    """
    current = bump_toml.value_as_string(value)
    if current is None or current == replacement_text:
        return None
    literal, multiline = _string_format_flags(value)
    if "\r" in replacement_text:
        # Multiline strings accept bare carriage returns in some supported
        # tomlkit versions, but TOML treats CR as a line ending only in CRLF.
        # A basic string escapes it consistently across the supported range.
        literal, multiline = False, False
    try:
        replacement = tomlkit.string(
            replacement_text,
            literal=literal,
            multiline=multiline,
        )
    except InvalidStringError:
        if not literal:
            raise
        multiline = False
        replacement = tomlkit.string(replacement_text)
    if multiline:
        replacement = _preserve_multiline_leading_newline(replacement, replacement_text)
    _copy_string_trivia(replacement, value)
    return replacement


def _copy_string_trivia(replacement: _Item, original: object) -> None:
    """Preserve comments and whitespace from an original TOML item."""
    match original:
        case _Item() as original_item:
            with suppress(AttributeError):
                replacement._trivia = original_item._trivia


def _string_format_flags(value: object) -> tuple[bool, bool]:
    """Return whether a TOML string uses literal and multiline delimiters."""
    match value:
        case _String() as styled_string:
            original_text = styled_string.as_string()
            return original_text.startswith("'"), original_text.startswith((
                "'''",
                '"""',
            ))
        case _:
            return False, False


def _preserve_multiline_leading_newline(
    replacement: _Item,
    replacement_text: str,
) -> _Item:
    """Work around tomlkit normalizing a leading multiline string newline."""
    if not replacement_text.startswith(("\n", "\r\n")):
        return replacement
    rendered = replacement.as_string()
    reparsed = tomlkit.parse(f"value = {rendered}")["value"]
    if str(reparsed) == replacement_text:
        return replacement
    repaired = f"{rendered[:3]}\n{rendered[3:]}"
    return tomlkit.parse(f"value = {repaired}")["value"]


def _selected_string_value_targets(
    table: _TableLike,
    field: str | None,
    *,
    table_path: tuple[str, ...],
) -> cabc.Generator[tuple[tuple[str, ...], _TableLike, str, object]]:
    """Yield each selected value with its container and location."""
    if field is None:
        yield from _direct_string_targets(table, table_path)
        return
    yield from _field_string_targets(table, field, table_path)


def _direct_string_targets(
    table: _TableLike,
    table_path: tuple[str, ...],
) -> cabc.Generator[tuple[tuple[str, ...], _TableLike, str, object]]:
    """Yield direct table values as rewrite targets."""
    for key, value in tuple(table.items()):
        yield (*table_path, key), table, key, value


def _field_string_targets(
    table: _TableLike,
    field: str,
    table_path: tuple[str, ...],
) -> cabc.Generator[tuple[tuple[str, ...], _TableLike, str, object]]:
    """Yield one field from each sub-table as a rewrite target."""
    for key, entry in tuple(table.items()):
        if isinstance(entry, _TABLE_TYPES) and field in entry:
            yield (*table_path, key, field), entry, field, entry[field]


def _update_string_values(
    table: _TableLike,
    replacement: cabc.Callable[[str], str],
    *,
    field: str | None = None,
) -> bool:
    """Apply one replacement callable to the selected string values."""
    changed = False
    for _location, container, key, value, text in _changed_string_targets(
        table, replacement, field=field
    ):
        if _replace_selected_string_value(container, key, value, text):
            changed = True
    return changed


def _changed_string_targets(
    table: _TableLike,
    replacement: cabc.Callable[[str], str],
    *,
    field: str | None = None,
    table_path: tuple[str, ...] = (),
) -> cabc.Generator[tuple[tuple[str, ...], _TableLike, str, object, str]]:
    """Yield selected values whose replacement text differs from the source."""
    for location, container, key, value in _selected_string_value_targets(
        table, field, table_path=table_path
    ):
        current = bump_toml.value_as_string(value)
        if current is None:
            continue
        replacement_text = replacement(current)
        if replacement_text == current:
            continue
        yield location, container, key, value, replacement_text


def _replace_selected_string_value(
    container: _TableLike,
    key: str,
    value: object,
    replacement_text: str,
) -> bool:
    """Replace one selected string while retaining its TOML style and trivia."""
    replacement_item = _prepare_string_value_replacement(value, replacement_text)
    if replacement_item is None:
        return False
    container[key] = replacement_item  # type: ignore[index]  # Table proxies support item assignment
    return True
