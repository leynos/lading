"""Sequence coercion helpers for TOML values (tuples and matrices)."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from lading.toml_coerce._core import _ErrorType, _reject


@typ.overload
def expect_sequence(
    value: object,
    field_name: str,
    *,
    error: _ErrorType,
    allow_none: typ.Literal[False] = False,
) -> cabc.Sequence[object]:
    """Require a sequence when ``allow_none`` is ``False``."""
    ...  # pylint: disable=unnecessary-ellipsis


@typ.overload
def expect_sequence(
    value: object,
    field_name: str,
    *,
    error: _ErrorType,
    allow_none: typ.Literal[True],
) -> cabc.Sequence[object] | None:
    """Allow ``None`` when ``allow_none`` is ``True``."""
    ...  # pylint: disable=unnecessary-ellipsis


def expect_sequence(
    value: object,
    field_name: str,
    *,
    error: _ErrorType,
    allow_none: bool = False,
) -> cabc.Sequence[object] | None:
    """Ensure ``value`` is a non-string sequence (optionally ``None``)."""
    if value is None:
        if allow_none:
            return None
        message = f"{field_name} must be a sequence"
        raise error(message)
    if isinstance(value, cabc.Sequence) and not isinstance(
        value, str | bytes | bytearray
    ):
        return value
    raise _reject(value, field_name, "a sequence", error)


def is_non_empty_sequence(value: object) -> bool:
    """Return ``True`` when ``value`` is a non-string sequence with content."""
    if not isinstance(value, cabc.Sequence):
        return False
    if isinstance(value, str | bytes | bytearray):
        return False
    return bool(value)


def validate_string_sequence(
    sequence: cabc.Sequence[typ.Any], field_name: str, *, error: _ErrorType
) -> tuple[str, ...]:
    """Validate that ``sequence`` contains only strings and return them."""
    items: list[str] = []
    for index, entry in enumerate(sequence):
        if not isinstance(entry, str):
            raise _reject(entry, f"{field_name}[{index}]", "a string", error)
        items.append(entry)
    return tuple(items)


def string_tuple(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[str, ...]:
    """Return a tuple of strings derived from ``value``."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, cabc.Sequence) and not isinstance(value, str | bytes):
        return validate_string_sequence(value, field_name, error=error)
    raise _reject(value, field_name, "a string or a sequence of strings", error)


def _validate_matrix_entry(
    entry: object,
    field_name: str,
    index: int,
    error: _ErrorType,
) -> tuple[str, ...]:
    """Validate and convert a single matrix entry to a string tuple."""
    if isinstance(entry, cabc.Sequence) and not isinstance(entry, str | bytes):
        return validate_string_sequence(entry, f"{field_name}[{index}]", error=error)
    raise _reject(entry, f"{field_name}[{index}]", "a sequence of strings", error)


def string_matrix(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[tuple[str, ...], ...]:
    """Return a tuple-of-tuples parsed from ``value`` as string sequences."""
    if value is None:
        return ()
    if not isinstance(value, cabc.Sequence) or isinstance(value, str | bytes):
        message = f"{field_name} must be a sequence of string sequences."
        raise error(message)
    return tuple(
        _validate_matrix_entry(entry, field_name, index, error)
        for index, entry in enumerate(value)
    )
