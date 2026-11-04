"""Tests for retained Cargo comparator checks."""

import pytest

from lading.commands.bump_requirement_bounds import excluding_retained_bounds


@pytest.mark.parametrize(
    ("bound", "target", "outcome"),
    [
        ("<0.7", "0.7.0", "excluded"),
        ("<0.7", "0.6.99", "accepted"),
        ("<=0.7", "0.7.999", "accepted"),
        ("<=0.7", "0.8.0", "excluded"),
        (">0.7", "0.7.999", "excluded"),
        (">0.7", "0.8.0", "accepted"),
        (">=0.7", "0.7.1", "accepted"),
        (">=0.7", "0.7.0-beta1", "excluded"),
        ("=1.2", "1.2.3-alpha", "excluded"),
        (">=1.2.3-beta4", "1.2.3-beta3", "excluded"),
        (">=1.2.3-beta4", "1.2.3+build.9", "accepted"),
    ],
)
def test_excluding_retained_bounds_matches_cargo_comparators(
    bound: str,
    target: str,
    outcome: str,
) -> None:
    """Partial and full relational bounds follow Cargo's comparator rules."""
    result = excluding_retained_bounds(f">=0.1.0, {bound}", target)

    assert bool(result) is (outcome == "excluded"), (
        f"Cargo bound {bound!r} should {outcome} target version {target!r}"
    )
