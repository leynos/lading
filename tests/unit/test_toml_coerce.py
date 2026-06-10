"""Property and snapshot tests for :mod:`lading.toml_coerce` (issue #108)."""

from __future__ import annotations

import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given

from lading import toml_coerce
from lading.config import ConfigurationError
from lading.workspace.models import WorkspaceModelError

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_ERRORS = (ConfigurationError, WorkspaceModelError)
_error_type = st.sampled_from(_ERRORS)
_non_string = st.one_of(st.integers(), st.booleans(), st.floats(allow_nan=False))
_strings = st.text(max_size=12)


@given(value=_strings, error=_error_type)
def test_expect_string_passes_strings_through(
    value: str, error: type[Exception]
) -> None:
    """Well-typed strings pass through unchanged."""
    assert toml_coerce.expect_string(value, "f", error=error) == value


@given(value=_non_string, error=_error_type)
def test_expect_string_rejects_with_canonical_shape(
    value: object, error: type[Exception]
) -> None:
    """Ill-typed values raise the bound error with the canonical message."""
    with pytest.raises(error) as excinfo:
        toml_coerce.expect_string(value, "demo.field", error=error)

    message = str(excinfo.value)
    assert message.startswith("demo.field must be a string; received ")
    assert type(value).__name__ in message


@given(values=st.lists(_strings, max_size=6), error=_error_type)
def test_string_tuple_round_trips_sequences(
    values: list[str], error: type[Exception]
) -> None:
    """String sequences coerce to equal tuples; None yields empty."""
    assert toml_coerce.string_tuple(values, "f", error=error) == tuple(values)
    assert toml_coerce.string_tuple(None, "f", error=error) == ()


@given(
    values=st.lists(_strings, max_size=3),
    bad_index=st.integers(min_value=0, max_value=3),
    bad=_non_string,
    error=_error_type,
)
def test_string_tuple_rejects_non_string_entries(
    values: list[str],
    bad_index: int,
    bad: object,
    error: type[Exception],
) -> None:
    """A non-string entry is rejected with its index in the field name."""
    position = min(bad_index, len(values))
    mixed: list[object] = [*values]
    mixed.insert(position, bad)

    with pytest.raises(error) as excinfo:
        toml_coerce.string_tuple(mixed, "demo.list", error=error)

    assert f"demo.list[{position}] must be a string" in str(excinfo.value)
    assert type(bad).__name__ in str(excinfo.value)


@given(value=st.one_of(st.none(), st.booleans()), error=_error_type)
def test_boolean_accepts_bools_and_default(
    *,
    value: bool | None,
    error: type[Exception],
) -> None:
    """Booleans pass through; None takes the default."""
    result = toml_coerce.boolean(value, "f", error=error, default=True)
    assert result is (True if value is None else value)


@given(value=st.integers(min_value=0, max_value=999), error=_error_type)
def test_non_negative_int_accepts_valid_values(
    value: int, error: type[Exception]
) -> None:
    """Non-negative integers pass through; None takes the default."""
    assert toml_coerce.non_negative_int(value, "f", 7, error=error) == value
    assert toml_coerce.non_negative_int(None, "f", 7, error=error) == 7


@given(value=st.integers(max_value=-1), error=_error_type)
def test_non_negative_int_rejects_negative(value: int, error: type[Exception]) -> None:
    """Negative integers raise the bound error type."""
    with pytest.raises(error, match="must be non-negative"):
        toml_coerce.non_negative_int(value, "f", 0, error=error)


@given(error=_error_type)
def test_mapping_helpers_reject_non_mappings(error: type[Exception]) -> None:
    """Mapping coercers raise the bound error type for non-mappings."""
    with pytest.raises(error):
        toml_coerce.expect_mapping([1], "f", error=error)
    with pytest.raises(error):
        toml_coerce.optional_mapping([1], "f", error=error)
    assert toml_coerce.optional_mapping(None, "f", error=error) is None


def test_coercion_error_messages_are_stable(snapshot: SnapshotAssertion) -> None:
    """Representative coercion messages change only deliberately."""
    cases: list[str] = []
    for call in (
        lambda: toml_coerce.expect_string(1, "bump.exclude[0]", error=_ERRORS[0]),
        lambda: toml_coerce.expect_mapping([], "packages[]", error=_ERRORS[1]),
        lambda: toml_coerce.expect_sequence("x", "packages", error=_ERRORS[1]),
        lambda: toml_coerce.string_tuple(1, "bump.exclude", error=_ERRORS[0]),
        lambda: toml_coerce.string_mapping(1, "preflight.env", error=_ERRORS[0]),
        lambda: toml_coerce.string_matrix(1, "preflight.aux_build", error=_ERRORS[0]),
        lambda: toml_coerce.boolean(1, "bump.rebuild_lockfiles", error=_ERRORS[0]),
        lambda: toml_coerce.non_negative_int(
            "x", "preflight.stderr_tail_lines", 0, error=_ERRORS[0]
        ),
    ):
        with pytest.raises(tuple(_ERRORS)) as excinfo:
            call()
        cases.append(str(excinfo.value))

    assert snapshot == cases
