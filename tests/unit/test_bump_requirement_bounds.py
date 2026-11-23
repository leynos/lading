"""Tests for retained Cargo comparator checks."""

import pytest

from lading.commands.bump_requirement_bounds import (
    excluding_retained_bounds,
    excluding_rewritten_first_bound,
)


@pytest.mark.parametrize(
    ("bound", "target", "expected"),
    [
        ("<0.7", "0.7.0", ("<0.7",)),
        ("<0.7", "0.6.99", ()),
        ("<=0.7", "0.7.999", ()),
        ("<=0.7", "0.8.0", ("<=0.7",)),
        (">0.7", "0.7.999", (">0.7",)),
        (">0.7", "0.8.0", ()),
        (">=0.7", "0.7.1", ()),
        (">=0.7", "0.7.0-beta1", (">=0.7",)),
        ("=1.2", "1.2.3-alpha", ("=1.2",)),
        (">=1.2.3-beta4", "1.2.3-beta3", (">=1.2.3-beta4",)),
        (">=1.2.3-beta4", "1.2.3+build.9", ()),
    ],
)
def test_excluding_retained_bounds_matches_cargo_comparators(
    bound: str,
    target: str,
    expected: tuple[str, ...],
) -> None:
    """Partial and full relational bounds follow Cargo's comparator rules."""
    result = excluding_retained_bounds(f">=0.1.0, {bound}", target)

    assert result == expected, (
        f"Cargo bound {bound!r} should report {expected!r} for target {target!r}"
    )


def test_numeric_prerelease_comparison_avoids_integer_conversion() -> None:
    """Long numeric identifiers retain SemVer ordering beyond Python's int limit."""
    bound_identifier = "9" * 5000
    target_identifier = "1" + "0" * 5000
    bound = f"<1.0.0-{bound_identifier}"
    requirement = f">=0.1.0, {bound}"

    result = excluding_retained_bounds(requirement, f"1.0.0-{target_identifier}")

    assert result == (bound,), (
        "a longer numeric prerelease target must sort after the shorter bound"
    )


@pytest.mark.parametrize(
    ("bound", "target", "expected"),
    [
        ("<2.*", "2.0.0", ("<2.*",)),
        ("<2.*", "1.9.0", ()),
        ("<2.3.*", "2.3.0", ("<2.3.*",)),
        ("<2.x", "2.0.0", ("<2.x",)),
    ],
)
def test_retained_wildcard_precision_uses_the_numeric_prefix(
    bound: str,
    target: str,
    expected: tuple[str, ...],
) -> None:
    """Wildcard components have the same comparison precision as omission."""
    result = excluding_retained_bounds(f">=0.1.0, {bound}", target)

    assert result == expected, (
        f"Cargo wildcard bound {bound!r} should report {expected!r}"
    )


@pytest.mark.parametrize(
    ("bound", "target", "expected"),
    [
        (
            "<18446744073709551615.0.0",
            "18446744073709551615.0.0",
            ("<18446744073709551615.0.0",),
        ),
        ("<18446744073709551616.0.0", "1.0.0", ()),
        (f"<{'9' * 5000}.0.0", "1.0.0", ()),
    ],
    ids=["u64-maximum", "u64-overflow", "oversized-decimal"],
)
def test_retained_bounds_reject_core_components_above_u64(
    bound: str,
    target: str,
    expected: tuple[str, ...],
) -> None:
    """Core components follow Cargo's u64 range without huge int conversions."""
    result = excluding_retained_bounds(f">=0.1.0, {bound}", target)

    assert result == expected, (
        f"Cargo bound {bound!r} should report {expected!r} for target {target!r}"
    )


@pytest.mark.parametrize(
    ("requirement", "target", "excluded"),
    [
        ("<0.7", "0.6.0", ("<0.6.0",)),
        (">0.6.0-beta4", "0.6.0", (">0.6.0",)),
        (">=0.6.0-beta4", "0.6.0", ()),
        ("<=0.7", "0.6.0", ()),
    ],
)
def test_excluding_rewritten_first_bound_detects_strict_operators(
    requirement: str,
    target: str,
    excluded: tuple[str, ...],
) -> None:
    """A rewritten strict first operator rejects its target."""
    result = excluding_rewritten_first_bound(requirement, target)

    assert result == excluded, (
        f"rewritten first comparator for {requirement!r} should report "
        f"{excluded!r}, got {result!r}"
    )
