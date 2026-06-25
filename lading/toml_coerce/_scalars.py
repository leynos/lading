"""Scalar coercion helpers for TOML values (strings, booleans, integers)."""

from __future__ import annotations

import typing as typ

from lading.toml_coerce._core import _ErrorType, _reject


def expect_string(value: object, field_name: str, *, error: _ErrorType) -> str:
    """Return ``value`` when it is a string, otherwise raise ``error``."""
    if isinstance(value, str):
        return value
    raise _reject(value, field_name, "a string", error)


def boolean(
    value: object, field_name: str, *, error: _ErrorType, default: bool = False
) -> bool:
    """Return a boolean parsed from ``value`` or ``default`` when ``None``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise _reject(value, field_name, "a boolean", error)


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
