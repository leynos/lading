"""Mapping coercion helpers for TOML tables."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from lading.toml_coerce._core import _ErrorType, _reject


def expect_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> cabc.Mapping[str, typ.Any]:
    """Return ``value`` as a string-keyed mapping or raise ``error``.

    Parameters
    ----------
    value : object
        The value to coerce.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is not a mapping.

    Returns
    -------
    collections.abc.Mapping[str, typing.Any]
        ``value`` unchanged when it is a mapping.

    Raises
    ------
    LadingError
        When ``value`` is not a :class:`collections.abc.Mapping`.
    """
    match value:
        case cabc.Mapping():
            return typ.cast("cabc.Mapping[str, typ.Any]", value)
        case _:
            raise _reject(value, field_name, "a mapping", error)


def optional_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> cabc.Mapping[str, typ.Any] | None:
    """Return ``value`` as a mapping when provided, or ``None``.

    Parameters
    ----------
    value : object
        The value to coerce. ``None`` passes through unchanged.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is neither ``None`` nor a
        mapping.

    Returns
    -------
    collections.abc.Mapping[str, typing.Any] | None
        The mapping, or ``None`` when ``value`` is ``None``.

    Raises
    ------
    LadingError
        When ``value`` is neither ``None`` nor a mapping.
    """
    if value is None:
        return None
    return expect_mapping(value, field_name, error=error)


def _validate_string_pair(
    key: object, raw_value: object, field_name: str, error: _ErrorType
) -> tuple[str, str]:
    """Validate and return a string key-value pair for ``field_name``."""
    match key:
        case str():
            match raw_value:
                case str():
                    return (key, raw_value)
                case _:
                    raise _reject(raw_value, f"{field_name}[{key}]", "a string", error)
        case _:
            message = (
                f"{field_name} keys must be strings; received {type(key).__name__}."
            )
            raise error(message)


def string_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[tuple[str, str], ...]:
    """Return key/value string pairs derived from mapping ``value``.

    Parameters
    ----------
    value : object
        The value to coerce. ``None`` yields an empty tuple.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when validation fails.

    Returns
    -------
    tuple[tuple[str, str], ...]
        The validated ``(key, value)`` string pairs; empty when ``value`` is
        ``None``.

    Raises
    ------
    LadingError
        When ``value`` is not a TOML table, or when any key or value is not a
        string.
    """
    match value:
        case None:
            return ()
        case cabc.Mapping():
            return tuple(
                _validate_string_pair(key, raw_value, field_name, error)
                for key, raw_value in value.items()
            )
        case _:
            raise _reject(value, field_name, "a TOML table", error)
