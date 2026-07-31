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
    """Ensure ``value`` is a non-string sequence (optionally ``None``).

    Parameters
    ----------
    value : object
        The value to coerce.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is not an accepted sequence.
    allow_none : bool, optional
        When ``True``, a ``None`` value is returned as ``None`` instead of
        being rejected (default ``False``).

    Returns
    -------
    cabc.Sequence[object] | None
        The sequence itself, or ``None`` when ``allow_none`` is set and
        ``value`` is ``None``.

    Raises
    ------
    LadingError
        The configured ``error`` subclass (built by the ``error`` factory) is
        raised if ``value`` is a string-like value or otherwise not a
        sequence.

    Examples
    --------
    >>> expect_sequence(None, "field", error=ValueError, allow_none=True) is None
    True
    >>> try:
    ...     expect_sequence(None, "field", error=ValueError)
    ... except ValueError as exc:
    ...     print("rejected")
    rejected
    """
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
    """Return ``True`` when ``value`` is a non-string sequence with content.

    Parameters
    ----------
    value : object
        The candidate to test.

    Returns
    -------
    bool
        ``True`` for a non-empty, non-string sequence, ``False`` otherwise.

    Examples
    --------
    >>> is_non_empty_sequence(["a"])
    True
    >>> is_non_empty_sequence([])
    False
    >>> is_non_empty_sequence("abc")
    False
    """
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
    """Validate that ``sequence`` contains only strings and return them.

    Parameters
    ----------
    sequence : cabc.Sequence[typ.Any]
        The sequence whose entries are validated.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when an entry is not a string.

    Returns
    -------
    tuple[str, ...]
        The validated entries as a tuple of strings.

    Raises
    ------
    LadingError
        The configured ``error`` subclass (built by the ``error`` factory) is
        raised if any entry is not a string.

    Examples
    --------
    >>> validate_string_sequence(["a", "b"], "field", error=ValueError)
    ('a', 'b')
    >>> try:
    ...     validate_string_sequence(["a", 1], "field", error=ValueError)
    ... except ValueError as exc:
    ...     print("rejected")
    rejected
    """
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
    """Return a tuple of strings derived from ``value``.

    Parameters
    ----------
    value : object
        The value to coerce. Accepts ``None``, a bare string, or a sequence
        of strings.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is not one of the accepted
        forms.

    Returns
    -------
    tuple[str, ...]
        The empty tuple for ``None``, a single-element tuple for a string,
        or the validated strings of a sequence.

    Raises
    ------
    LadingError
        The configured ``error`` subclass (built by the ``error`` factory) is
        raised if ``value`` is ``bytes`` or otherwise not a string or
        string sequence.

    Examples
    --------
    >>> string_tuple("a", "field", error=ValueError)
    ('a',)
    >>> string_tuple(["a", "b"], "field", error=ValueError)
    ('a', 'b')
    """
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
    """Return a tuple-of-tuples parsed from ``value`` as string sequences.

    Parameters
    ----------
    value : object
        The value to coerce. Accepts ``None`` or a sequence of string
        sequences.
    field_name : str
        Name of the field, used in error messages.
    error : _ErrorType
        Exception factory called when ``value`` is not one of the accepted
        forms.

    Returns
    -------
    tuple[tuple[str, ...], ...]
        The empty tuple for ``None``, otherwise each element validated as a
        sequence of strings.

    Raises
    ------
    LadingError
        The configured ``error`` subclass (built by the ``error`` factory) is
        raised if ``value`` is string-like or otherwise not a sequence of
        string sequences.

    Examples
    --------
    >>> string_matrix([["a", "b"], ["c"]], "field", error=ValueError)
    (('a', 'b'), ('c',))
    """
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
