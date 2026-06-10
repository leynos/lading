"""Shared TOML scalar, sequence, and mapping coercion helpers.

This module is the canonical home (issue #108) for the coercion idiom shared
by :mod:`lading.config` and :mod:`lading.workspace.models`. Each helper
accepts the ``error`` keyword naming the :class:`~lading.exceptions.LadingError`
subclass to raise, so both modules keep their domain-specific exception
types while sharing one implementation and one error-message shape:

``{field} must be {expected}; received {type(value).__name__}.``

Consumers bind their error type once with :func:`functools.partial` rather
than re-declaring the helpers.

Examples
--------
>>> import functools
>>> from lading.exceptions import LadingError
>>> expect = functools.partial(expect_string, error=LadingError)
>>> expect("hello", "demo.field")
'hello'
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

if typ.TYPE_CHECKING:
    from lading.exceptions import LadingError

_ErrorType = type["LadingError"]


def _reject(
    value: object, field_name: str, expected: str, error: _ErrorType
) -> typ.NoReturn:
    """Raise ``error`` with the canonical coercion message shape."""
    message = f"{field_name} must be {expected}; received {type(value).__name__}."
    raise error(message)


def expect_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> cabc.Mapping[str, typ.Any]:
    """Return ``value`` as a string-keyed mapping or raise ``error``."""
    if isinstance(value, cabc.Mapping):
        return typ.cast("cabc.Mapping[str, typ.Any]", value)
    _reject(value, field_name, "a mapping", error)


def optional_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> cabc.Mapping[str, typ.Any] | None:
    """Return ``value`` as a mapping when provided, or ``None``."""
    if value is None:
        return None
    return expect_mapping(value, field_name, error=error)


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
    _reject(value, field_name, "a sequence", error)


def expect_string(value: object, field_name: str, *, error: _ErrorType) -> str:
    """Return ``value`` when it is a string, otherwise raise ``error``."""
    if isinstance(value, str):
        return value
    _reject(value, field_name, "a string", error)


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
            _reject(entry, f"{field_name}[{index}]", "a string", error)
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
    _reject(value, field_name, "a string or a sequence of strings", error)


def _validate_matrix_entry(
    entry: object,
    field_name: str,
    index: int,
    error: _ErrorType,
) -> tuple[str, ...]:
    """Validate and convert a single matrix entry to a string tuple."""
    if isinstance(entry, cabc.Sequence) and not isinstance(entry, str | bytes):
        return validate_string_sequence(entry, f"{field_name}[{index}]", error=error)
    _reject(entry, f"{field_name}[{index}]", "a sequence of strings", error)


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


def _validate_string_pair(
    key: object, raw_value: object, field_name: str, error: _ErrorType
) -> tuple[str, str]:
    """Validate and return a string key-value pair for ``field_name``."""
    if not isinstance(key, str):
        message = f"{field_name} keys must be strings; received {type(key).__name__}."
        raise error(message)
    if not isinstance(raw_value, str):
        _reject(raw_value, f"{field_name}[{key}]", "a string", error)
    return (key, raw_value)


def string_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[tuple[str, str], ...]:
    """Return key/value string pairs derived from mapping ``value``."""
    if value is None:
        return ()
    if not isinstance(value, cabc.Mapping):
        _reject(value, field_name, "a TOML table", error)
    return tuple(
        _validate_string_pair(key, raw_value, field_name, error)
        for key, raw_value in value.items()
    )


def boolean(
    value: object, field_name: str, *, error: _ErrorType, default: bool = False
) -> bool:
    """Return a boolean parsed from ``value`` or ``default`` when ``None``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    _reject(value, field_name, "a boolean", error)


def non_negative_int(
    value: object, field_name: str, default: int, *, error: _ErrorType
) -> int:
    """Return a non-negative integer parsed from ``value`` or ``default``."""
    if value is None:
        return default
    try:
        integer = int(typ.cast("typ.Any", value))
    except (TypeError, ValueError) as exc:  # pragma: no cover - validation guard
        message = f"{field_name} must be an integer; received {type(value).__name__}."
        raise error(message) from exc
    if integer < 0:
        message = f"{field_name} must be non-negative."
        raise error(message)
    return integer


__all__ = [
    "boolean",
    "expect_mapping",
    "expect_sequence",
    "expect_string",
    "is_non_empty_sequence",
    "non_negative_int",
    "optional_mapping",
    "string_mapping",
    "string_matrix",
    "string_tuple",
    "validate_string_sequence",
]
