"""Scalar coercion helpers for TOML values (strings, booleans, integers)."""

from __future__ import annotations

from lading.toml_coerce._core import _ErrorType, _reject


def expect_string(value: object, field_name: str, *, error: _ErrorType) -> str:
    """Return ``value`` when it is a string, otherwise raise ``error``.

    Parameters
    ----------
    value : object
        The value to coerce.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is not a string.

    Returns
    -------
    str
        ``value`` unchanged.

    Raises
    ------
    LadingError
        When ``value`` is not a :class:`str`.
    """
    match value:
        case str():
            return value
        case _:
            raise _reject(value, field_name, "a string", error)


def boolean(
    value: object, field_name: str, *, error: _ErrorType, default: bool = False
) -> bool:
    """Return a boolean parsed from ``value`` or ``default`` when ``None``.

    Parameters
    ----------
    value : object
        The value to coerce.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is neither ``None`` nor a bool.
    default : bool, optional
        Value returned when ``value`` is ``None`` (default ``False``).

    Returns
    -------
    bool
        The boolean, or ``default`` when ``value`` is ``None``.

    Raises
    ------
    LadingError
        When ``value`` is neither ``None`` nor a :class:`bool`.
    """
    match value:
        case None:
            return default
        case bool():
            return value
        case _:
            raise _reject(value, field_name, "a boolean", error)


def non_negative_int(
    value: object, field_name: str, default: int, *, error: _ErrorType
) -> int:
    """Return a non-negative integer parsed from ``value`` or ``default``.

    Parameters
    ----------
    value : object
        The value to coerce. ``None`` selects ``default``.
    field_name : str
        Name of the field, used in error messages.
    default : int
        Value returned when ``value`` is ``None``.
    error : _ErrorType
        Exception factory called when validation fails.

    Returns
    -------
    int
        A non-negative integer parsed from ``value``, or ``default``.

    Raises
    ------
    LadingError
        When ``value`` is not a real integer (``bool`` and ``float`` are
        rejected) or an integer-valued string, or when the result is negative.
    """
    type_error = f"{field_name} must be an integer; received {type(value).__name__}."
    # ``bool`` is a subclass of ``int`` and ``float``/other types are truthy for
    # a blanket ``int(...)`` cast, so dispatch explicitly to accept only real
    # integers and integer-valued strings (the config string path).
    match value:
        case None:
            return default
        case bool():
            raise error(type_error)
        case int():
            integer = value
        case str():
            try:
                integer = int(value)
            except ValueError as exc:
                raise error(type_error) from exc
        case _:
            raise error(type_error)
    if integer < 0:
        message = f"{field_name} must be non-negative."
        raise error(message)
    return integer
