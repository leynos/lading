"""Unit tests for bump crate-selector and skip helpers."""

from __future__ import annotations

from lading.commands import bump


def test_determine_package_selectors_respects_exclusions() -> None:
    """Excluded crates produce no package selectors."""
    assert bump._determine_package_selectors("beta", {"beta"}) == (), (
        "an excluded crate should yield no package selectors"
    )


def test_determine_package_selectors_includes_package_for_active_crates() -> None:
    """Active crates receive the package selector tuple."""
    assert bump._determine_package_selectors("beta", set()) == (("package",),), (
        "an active crate should yield the package selector"
    )


def test_should_skip_crate_update_requires_selectors_or_dependencies() -> None:
    """Skipping occurs only when both selectors and dependency sections are empty."""
    assert bump._should_skip_crate_update((), {}) is True, (
        "no selectors and no dependency sections should skip the crate"
    )
    assert (
        bump._should_skip_crate_update((("package",),), {"dependencies": ("alpha",)})
        is False
    ), "a crate with selectors or dependency sections should not be skipped"
