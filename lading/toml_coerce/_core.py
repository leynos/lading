"""Shared error-building primitive for the TOML coercion helpers.

Holds the :class:`~lading.exceptions.LadingError` forward reference, the
``_ErrorType`` alias naming the subclass to raise, and the :func:`_reject`
helper that produces the canonical coercion message shape:

``{field} must be {expected}; received {type(value).__name__}.``
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from lading.exceptions import LadingError

_ErrorType = type["LadingError"]


def _reject(
    value: object, field_name: str, expected: str, error: _ErrorType
) -> LadingError:
    """Build ``error`` with the canonical coercion message shape.

    Callers ``raise`` the returned exception so each coercion helper
    terminates explicitly on the failure path, which keeps the helpers
    legible to linters and type checkers now that this primitive lives in a
    separate module from its call sites.
    """
    message = f"{field_name} must be {expected}; received {type(value).__name__}."
    return error(message)
