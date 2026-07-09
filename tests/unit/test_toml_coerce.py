"""Property and snapshot tests for :mod:`lading.toml_coerce` (issue #108)."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given

from lading import toml_coerce
from lading.config import ConfigurationError
from lading.workspace.models import WorkspaceModelError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from syrupy.assertion import SnapshotAssertion

_ERRORS = (ConfigurationError, WorkspaceModelError)
_error_type = st.sampled_from(_ERRORS)
_non_string = st.one_of(st.integers(), st.booleans(), st.floats(allow_nan=False))
_strings = st.text(max_size=12)


@dc.dataclass(frozen=True, slots=True)
class _IndexedRejectionCase:
    """Inputs for asserting an indexed non-string rejection."""

    values: list[str]
    bad_index: int
    bad: object
    error: type[Exception]


def _assert_rejects_indexed_non_string(
    coerce: cabc.Callable[..., object],
    field_name: str,
    case: _IndexedRejectionCase,
) -> None:
    """Assert *coerce* rejects a non-string entry, naming its index in the field."""
    position = min(case.bad_index, len(case.values))
    mixed: list[object] = [*case.values]
    mixed.insert(position, case.bad)

    with pytest.raises(case.error) as excinfo:
        coerce(mixed, field_name, error=case.error)

    assert f"{field_name}[{position}] must be a string" in str(excinfo.value)
    assert type(case.bad).__name__ in str(excinfo.value)


class TestTomlCoerce:
    """Property and snapshot coverage for the coercion helpers."""

    @given(value=_strings, error=_error_type)
    def test_expect_string_passes_strings_through(
        self, value: str, error: type[Exception]
    ) -> None:
        """Well-typed strings pass through unchanged."""
        assert toml_coerce.expect_string(value, "f", error=error) == value

    @given(value=_non_string, error=_error_type)
    def test_expect_string_rejects_with_canonical_shape(
        self, value: object, error: type[Exception]
    ) -> None:
        """Ill-typed values raise the bound error with the canonical message."""
        with pytest.raises(error) as excinfo:
            toml_coerce.expect_string(value, "demo.field", error=error)

        message = str(excinfo.value)
        assert message.startswith("demo.field must be a string; received ")
        assert type(value).__name__ in message

    @given(values=st.lists(_strings, max_size=6), error=_error_type)
    def test_string_tuple_round_trips_sequences(
        self, values: list[str], error: type[Exception]
    ) -> None:
        """String sequences coerce to equal tuples; None yields empty."""
        assert toml_coerce.string_tuple(values, "f", error=error) == tuple(values)
        assert toml_coerce.string_tuple(None, "f", error=error) == ()

    @given(values=st.lists(_strings, max_size=6), error=_error_type)
    def test_validate_string_sequence_accepts_strings(
        self, values: list[str], error: type[Exception]
    ) -> None:
        """A sequence of only strings returns them as a tuple."""
        assert toml_coerce.validate_string_sequence(values, "f", error=error) == tuple(
            values
        )

    @given(
        values=st.lists(_strings, max_size=3),
        bad_index=st.integers(min_value=0, max_value=3),
        bad=_non_string,
        error=_error_type,
    )
    def test_string_tuple_rejects_non_string_entries(
        self,
        values: list[str],
        bad_index: int,
        bad: object,
        error: type[Exception],
    ) -> None:
        """A non-string entry is rejected with its index in the field name."""
        _assert_rejects_indexed_non_string(
            toml_coerce.string_tuple,
            "demo.list",
            _IndexedRejectionCase(values, bad_index, bad, error),
        )

    @given(
        values=st.lists(_strings, max_size=3),
        bad_index=st.integers(min_value=0, max_value=3),
        bad=_non_string,
        error=_error_type,
    )
    def test_validate_string_sequence_rejects_non_strings(
        self,
        values: list[str],
        bad_index: int,
        bad: object,
        error: type[Exception],
    ) -> None:
        """A non-string entry raises with its index in the field name."""
        _assert_rejects_indexed_non_string(
            toml_coerce.validate_string_sequence,
            "demo.seq",
            _IndexedRejectionCase(values, bad_index, bad, error),
        )

    @given(value=st.lists(_strings, max_size=6), error=_error_type)
    def test_expect_sequence_accepts_non_string_sequences(
        self, value: list[str], error: type[Exception]
    ) -> None:
        """Non-string sequences pass through unchanged."""
        assert toml_coerce.expect_sequence(value, "f", error=error) == value

    @given(error=_error_type)
    def test_expect_sequence_handles_none(self, error: type[Exception]) -> None:
        """``allow_none`` returns ``None``; otherwise ``None`` is rejected."""
        assert (
            toml_coerce.expect_sequence(None, "f", error=error, allow_none=True) is None
        )
        with pytest.raises(error) as excinfo:
            toml_coerce.expect_sequence(None, "f", error=error)
        assert str(excinfo.value) == "f must be a sequence; received NoneType."

    @given(
        value=st.one_of(_strings, st.binary(max_size=6), _non_string),
        error=_error_type,
    )
    def test_expect_sequence_rejects_strings_and_scalars(
        self, value: object, error: type[Exception]
    ) -> None:
        """Strings, bytes, and scalars raise the bound error type."""
        with pytest.raises(error):
            toml_coerce.expect_sequence(value, "demo.field", error=error)

    @given(value=st.lists(st.integers(), min_size=1, max_size=6))
    def test_is_non_empty_sequence_true_for_non_empty(self, value: list[int]) -> None:
        """Non-empty non-string sequences are recognised."""
        assert toml_coerce.is_non_empty_sequence(value) is True

    @given(
        value=st.one_of(
            st.just([]),
            st.just(()),
            _strings,
            st.binary(max_size=6),
            _non_string,
        )
    )
    def test_is_non_empty_sequence_false_otherwise(self, value: object) -> None:
        """Empty sequences, strings, bytes, and scalars are rejected."""
        assert toml_coerce.is_non_empty_sequence(value) is False

    @given(
        value=st.lists(st.lists(_strings, max_size=4), max_size=4),
        error=_error_type,
    )
    def test_string_matrix_round_trips_nested_sequences(
        self, value: list[list[str]], error: type[Exception]
    ) -> None:
        """Nested string sequences coerce to a tuple of tuples; None yields empty."""
        expected = tuple(tuple(row) for row in value)
        assert toml_coerce.string_matrix(value, "f", error=error) == expected
        assert toml_coerce.string_matrix(None, "f", error=error) == ()

    @given(value=st.one_of(_strings, _non_string), error=_error_type)
    def test_string_matrix_rejects_non_sequence_values(
        self, value: object, error: type[Exception]
    ) -> None:
        """A scalar or string top-level value raises the bound error type."""
        with pytest.raises(error) as excinfo:
            toml_coerce.string_matrix(value, "demo.matrix", error=error)
        message = str(excinfo.value)
        assert (
            "demo.matrix must be a sequence of string sequences; received " in message
        )
        assert type(value).__name__ in message

    @given(bad=_non_string, error=_error_type)
    def test_string_matrix_rejects_non_string_rows(
        self, bad: object, error: type[Exception]
    ) -> None:
        """A non-sequence row is rejected, naming its index in the field."""
        with pytest.raises(error) as excinfo:
            toml_coerce.string_matrix([["ok"], bad], "demo.matrix", error=error)
        message = str(excinfo.value)
        assert "demo.matrix[1] must be a sequence of strings; received " in message
        assert type(bad).__name__ in message

    @given(value=st.one_of(st.none(), st.booleans()), error=_error_type)
    def test_boolean_accepts_bools_and_default(
        self,
        *,
        value: bool | None,
        error: type[Exception],
    ) -> None:
        """Booleans pass through; None takes the default."""
        result = toml_coerce.boolean(value, "f", error=error, default=True)
        assert result is (True if value is None else value)

    @given(value=st.integers(min_value=0, max_value=999), error=_error_type)
    def test_non_negative_int_accepts_valid_values(
        self, value: int, error: type[Exception]
    ) -> None:
        """Non-negative ints and integer strings pass; None takes the default."""
        assert toml_coerce.non_negative_int(value, "f", 7, error=error) == value
        # Integer-valued strings still parse (the config string path).
        assert toml_coerce.non_negative_int(str(value), "f", 7, error=error) == value
        assert toml_coerce.non_negative_int(None, "f", 7, error=error) == 7

    @given(value=st.integers(max_value=-1), error=_error_type)
    def test_non_negative_int_rejects_negative(
        self, value: int, error: type[Exception]
    ) -> None:
        """Negative integers raise the bound error type."""
        with pytest.raises(error, match="must be non-negative"):
            toml_coerce.non_negative_int(value, "f", 0, error=error)

    @given(
        value=st.one_of(
            st.booleans(),
            st.floats(allow_nan=False, allow_infinity=False),
        ),
        error=_error_type,
    )
    def test_non_negative_int_rejects_non_integer_types(
        self, *, value: bool | float, error: type[Exception]
    ) -> None:
        """Booleans and floats (e.g. True, 3.9) are rejected, not coerced."""
        with pytest.raises(error, match="must be an integer"):
            toml_coerce.non_negative_int(value, "f", 0, error=error)

    @given(error=_error_type)
    def test_mapping_helpers_accept_and_reject_mappings(
        self, error: type[Exception]
    ) -> None:
        """Mapping coercers pass valid mappings through and reject non-mappings."""
        mapping = {"key": "value"}
        assert toml_coerce.expect_mapping(mapping, "f", error=error) is mapping
        assert toml_coerce.optional_mapping(mapping, "f", error=error) is mapping
        with pytest.raises(error):
            toml_coerce.expect_mapping([1], "f", error=error)
        with pytest.raises(error):
            toml_coerce.optional_mapping([1], "f", error=error)
        assert toml_coerce.optional_mapping(None, "f", error=error) is None

    def test_coercion_error_messages_are_stable(
        self, snapshot: SnapshotAssertion
    ) -> None:
        """Representative coercion messages change only deliberately."""
        cases: list[str] = []
        for call in (
            lambda: toml_coerce.expect_string(1, "bump.exclude[0]", error=_ERRORS[0]),
            lambda: toml_coerce.expect_mapping([], "packages[]", error=_ERRORS[1]),
            lambda: toml_coerce.expect_sequence("x", "packages", error=_ERRORS[1]),
            lambda: toml_coerce.string_tuple(1, "bump.exclude", error=_ERRORS[0]),
            lambda: toml_coerce.string_mapping(1, "preflight.env", error=_ERRORS[0]),
            lambda: toml_coerce.string_matrix(
                1, "preflight.aux_build", error=_ERRORS[0]
            ),
            lambda: toml_coerce.boolean(1, "bump.rebuild_lockfiles", error=_ERRORS[0]),
            lambda: toml_coerce.non_negative_int(
                "x", "preflight.stderr_tail_lines", 0, error=_ERRORS[0]
            ),
        ):
            with pytest.raises(tuple(_ERRORS)) as excinfo:
                call()
            cases.append(str(excinfo.value))

        assert snapshot == cases
