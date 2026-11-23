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
