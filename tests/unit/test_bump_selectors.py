"""Unit tests for bump crate-selector and skip helpers."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("selectors", "dependency_sections", "expected_skip"),
    [
        pytest.param((), {}, True, id="both_empty"),
        pytest.param((), {"dependencies": ("alpha",)}, False, id="dependencies_only"),
        pytest.param((("package",),), {}, False, id="selectors_only"),
        pytest.param(
            (("package",),),
            {"dependencies": ("alpha",)},
            False,
            id="both_present",
        ),
    ],
)
def test_should_skip_crate_update_requires_selectors_or_dependencies(
    *,
    selectors: tuple[tuple[str, ...], ...],
    dependency_sections: dict[str, tuple[str, ...]],
    expected_skip: bool,
) -> None:
    """A crate is skipped only when it has neither selectors nor dependencies."""
    result = bump._should_skip_crate_update(selectors, dependency_sections)
    assert result is expected_skip, (
        "skip should be True only when selectors and dependency sections are both empty"
    )
