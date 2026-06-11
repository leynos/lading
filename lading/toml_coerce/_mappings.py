"""Mapping coercion helpers for TOML tables."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from lading.toml_coerce._core import _ErrorType, _reject


def expect_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> cabc.Mapping[str, typ.Any]:
    """Return ``value`` as a string-keyed mapping or raise ``error``."""
    if isinstance(value, cabc.Mapping):
        return typ.cast("cabc.Mapping[str, typ.Any]", value)
    raise _reject(value, field_name, "a mapping", error)


def optional_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> cabc.Mapping[str, typ.Any] | None:
    """Return ``value`` as a mapping when provided, or ``None``."""
    if value is None:
        return None
    return expect_mapping(value, field_name, error=error)


def _validate_string_pair(
    key: object, raw_value: object, field_name: str, error: _ErrorType
) -> tuple[str, str]:
    """Validate and return a string key-value pair for ``field_name``."""
    if not isinstance(key, str):
        message = f"{field_name} keys must be strings; received {type(key).__name__}."
        raise error(message)
    if not isinstance(raw_value, str):
        raise _reject(raw_value, f"{field_name}[{key}]", "a string", error)
    return (key, raw_value)


def string_mapping(
    value: object, field_name: str, *, error: _ErrorType
) -> tuple[tuple[str, str], ...]:
    """Return key/value string pairs derived from mapping ``value``."""
    if value is None:
        return ()
    if not isinstance(value, cabc.Mapping):
        raise _reject(value, field_name, "a TOML table", error)
    return tuple(
        _validate_string_pair(key, raw_value, field_name, error)
        for key, raw_value in value.items()
    )
