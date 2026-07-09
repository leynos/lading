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
    match value:
        case None:
            if allow_none:
                return None
            raise _reject(value, field_name, "a sequence", error)
        case str() | bytes() | bytearray():
            raise _reject(value, field_name, "a sequence", error)
        case cabc.Sequence():
            return value
        case _:
            raise _reject(value, field_name, "a sequence", error)


def is_non_empty_sequence(value: object) -> bool:
    """Return ``True`` when ``value`` is a non-string sequence with content."""
    match value:
        case str() | bytes() | bytearray():
            return False
        case cabc.Sequence():
            return bool(value)
        case _:
            return False


def validate_string_sequence(
    sequence: cabc.Sequence[typ.Any], field_name: str, *, error: _ErrorType
) -> tuple[str, ...]:
    """Validate that ``sequence`` contains only strings and return them."""
    items: list[str] = []
    for index, entry in enumerate(sequence):
        match entry:
            case str():
                items.append(entry)
            case _:
                raise _reject(entry, f"{field_name}[{index}]", "a string", error)
    return tuple(items)


def string_tuple(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[str, ...]:
    """Return a tuple of strings derived from ``value``."""
    # ``bytearray`` is deliberately excluded from the string/bytes rejection so
    # it flows into ``validate_string_sequence`` (which rejects its int items),
    # preserving the pre-refactor behaviour.
    match value:
        case None:
            return ()
        case str():
            return (value,)
        case bytes():
            raise _reject(value, field_name, "a string or a sequence of strings", error)
        case cabc.Sequence():
            return validate_string_sequence(value, field_name, error=error)
        case _:
            raise _reject(value, field_name, "a string or a sequence of strings", error)


def _validate_matrix_entry(
    entry: object,
    field_name: str,
    index: int,
    error: _ErrorType,
) -> tuple[str, ...]:
    """Validate and convert a single matrix entry to a string tuple."""
    match entry:
        case str() | bytes():
            raise _reject(
                entry, f"{field_name}[{index}]", "a sequence of strings", error
            )
        case cabc.Sequence():
            return validate_string_sequence(
                entry, f"{field_name}[{index}]", error=error
            )
        case _:
            raise _reject(
                entry, f"{field_name}[{index}]", "a sequence of strings", error
            )


def string_matrix(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[tuple[str, ...], ...]:
    """Return a tuple-of-tuples parsed from ``value`` as string sequences."""
    match value:
        case None:
            return ()
        case str() | bytes():
            raise _reject(value, field_name, "a sequence of string sequences", error)
        case cabc.Sequence():
            return tuple(
                _validate_matrix_entry(entry, field_name, index, error)
                for index, entry in enumerate(value)
            )
        case _:
            raise _reject(value, field_name, "a sequence of string sequences", error)
