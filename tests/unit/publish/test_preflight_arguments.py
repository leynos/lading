"""Unit tests for publish preflight argument helpers."""

import string
import typing as typ
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from lading.commands import publish_preflight

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _make_options(
    *,
    test_excludes: tuple[str, ...] = (),
    unit_tests_only: bool = False,
) -> publish_preflight._CargoPreflightOptions:
    """Build preflight options with the given excludes and target selection."""
    return publish_preflight._CargoPreflightOptions(
        extra_args=(),
        test_excludes=test_excludes,
        unit_tests_only=unit_tests_only,
    )


def test_build_test_arguments_does_not_mutate_base_list() -> None:
    """The helper copies base arguments before applying mutations."""
    base = ["--workspace"]
    options = _make_options()

    result = publish_preflight._build_test_arguments(base, options)

    assert result is not base, (
        "the helper must return a new list rather than mutating the base in place"
    )
    assert base == ["--workspace"], (
        "the caller's base argument list must be left untouched"
    )


def test_build_test_arguments_with_no_unit_tests_and_no_excludes() -> None:
    """Empty options should preserve the base argument ordering."""
    base = ["--workspace"]
    options = _make_options(unit_tests_only=False, test_excludes=())

    result = publish_preflight._build_test_arguments(base, options)

    assert result is not base, (
        "the helper must return a new list rather than mutating the base in place"
    )
    assert result == ["--workspace"], (
        "with no options the base arguments must pass through unchanged"
    )


def test_build_test_arguments_appends_unit_test_flags_after_excludes() -> None:
    """Unit tests only mode inserts lib/bin flags after exclusions."""
    base = ["--workspace"]
    options = _make_options(
        unit_tests_only=True, test_excludes=("beta", " alpha ", "beta")
    )

    result = publish_preflight._build_test_arguments(base, options)

    assert result[1:5] == ["--exclude", "alpha", "--exclude", "beta"], (
        "normalized excludes must be emitted as sorted --exclude pairs after "
        "the base arguments"
    )
    assert result[5:] == ["--lib", "--bins"], (
        "unit-test target flags must come after every --exclude pair"
    )


def test_build_test_arguments_ignores_blank_excludes() -> None:
    """Whitespace-only entries are ignored when building test arguments."""
    base = ["--workspace"]
    options = _make_options(test_excludes=("", " ", "\t"))

    result = publish_preflight._build_test_arguments(base, options)

    assert "--exclude" not in result, (
        "whitespace-only entries must not produce an --exclude flag"
    )


def test_build_test_arguments_skips_blank_entries_with_valid_names() -> None:
    """Only the meaningful crate names produce --exclude flags."""
    base = ["--workspace"]
    options = _make_options(test_excludes=("  ", "alpha", "", "beta"))

    result = publish_preflight._build_test_arguments(base, options)

    assert result[-4:] == ["--exclude", "alpha", "--exclude", "beta"], (
        "only the meaningful crate names may produce --exclude flags"
    )


def test_normalize_test_excludes_sorts_and_deduplicates() -> None:
    """The normalization helper returns trimmed, sorted unique names."""
    entries = (" beta", "alpha ", "alpha", "gamma")

    assert publish_preflight._normalize_test_excludes(entries) == (
        "alpha",
        "beta",
        "gamma",
    ), (
        "normalization must strip whitespace, drop duplicates, and sort the "
        "surviving names"
    )


def test_normalize_test_excludes_handles_empty_values() -> None:
    """Blank strings are ignored when normalizing test excludes."""
    entries = ("", " \t", "alpha", "", "beta")

    assert publish_preflight._normalize_test_excludes(entries) == (
        "alpha",
        "beta",
    ), "blank entries must be dropped from the normalized excludes"


# ---------------------------------------------------------------------------
# Canonical preflight argument composition (issue #96)
# ---------------------------------------------------------------------------

_dir_name = st.text(
    alphabet=string.ascii_letters + string.digits + " -_!@#&",
    min_size=1,
    max_size=20,
).filter(lambda name: name.strip(" ") == name)


@given(unit_tests_only=st.booleans(), dir_name=_dir_name)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_preflight_argument_set_invariants(
    *, unit_tests_only: bool, dir_name: str
) -> None:
    """Composed cargo arguments satisfy the documented invariants.

    ``--workspace`` always leads, ``--target-dir`` always points at the
    supplied directory, ``--all-targets`` appears in the check set always and
    in the test set iff full test targets are requested, and composition is
    deterministic.
    """
    target_dir = Path("/preflight") / dir_name

    check_args, test_args = publish_preflight._preflight_argument_sets(
        target_dir, unit_tests_only=unit_tests_only
    )

    for arguments in (check_args, test_args):
        assert arguments[0] == "--workspace", (
            "every composed cargo argument set must lead with --workspace"
        )
        assert arguments[-1] == f"--target-dir={target_dir}", (
            "every composed cargo argument set must end with the supplied --target-dir"
        )
    assert "--all-targets" in check_args, (
        "the check set must always include --all-targets"
    )
    assert ("--all-targets" in test_args) == (not unit_tests_only), (
        "the test set must include --all-targets exactly when full test "
        "targets are requested"
    )
    assert (check_args, test_args) == publish_preflight._preflight_argument_sets(
        target_dir, unit_tests_only=unit_tests_only
    ), "argument composition must be deterministic for identical inputs"


@pytest.mark.parametrize("unit_tests_only", [True, False])
def test_preflight_argument_sets_snapshot(
    snapshot: SnapshotAssertion,
    *,
    unit_tests_only: bool,
) -> None:
    """The composed cargo argument tuples are locked by snapshot."""
    check_args, test_args = publish_preflight._preflight_argument_sets(
        Path("/preflight/target"), unit_tests_only=unit_tests_only
    )

    assert snapshot == {"check": check_args, "test": test_args}, (
        "the composed check and test argument tuples drifted from the recorded snapshot"
    )
