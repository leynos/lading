"""Unit tests for Cargo comparator analysis in configured rewrites.

The bump replaces the first requirement version but retains its operator and
any later comma-separated clauses. These tests pin the analysis that decides
which comparators reject the requested target, including the Cargo partial
version and wildcard forms the parser must ignore rather than misreport.
"""

import typing as typ

import pytest

from lading.commands import bump_manifest_rewrite_bounds

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((">=1.0.0, <=2.0.0", "2.0.0", ()), id="inclusive-upper-bound"),
        pytest.param((">=1.0.0, >1.5.0", "2.0.0", ()), id="strict-upper-accepts"),
        pytest.param(
            (">=1.0.0, >2.0.0", "2.0.0", (">2.0.0",)), id="strict-upper-rejects"
        ),
        pytest.param((">=1.0.0, =2.0.0", "2.0.0", ()), id="equality-accepts"),
        pytest.param((">=1.0.0, =1.0.0", "2.0.0", ("=1.0.0",)), id="equality-rejects"),
        pytest.param((">=1.0.0, >=2.0.0", "2.0.0", ()), id="inclusive-lower-accepts"),
        pytest.param(
            (">=1.0.0, >=2.1.0", "2.0.0", (">=2.1.0",)), id="inclusive-lower-rejects"
        ),
        pytest.param((">=1.0.0, <1.*", "2.0.0", ("<1.*",)), id="wildcard-rejects"),
        pytest.param((">=1.0.0, <1.2.3.*", "2.0.0", ()), id="full-wildcard-is-ignored"),
        pytest.param(
            (">=1.0.0, <1.*-alpha", "2.0.0", ()), id="wildcard-prerelease-ignored"
        ),
        pytest.param((">=1.0.0, <1.*+b", "2.0.0", ()), id="wildcard-build-ignored"),
        pytest.param(
            (">=1.0.0, <2.0.0-alpha.1", "2.0.0", ("<2.0.0-alpha.1",)),
            id="prerelease-bound-rejects-release",
        ),
        pytest.param(
            (">=1.0.0, <2.0.0-alpha.1", "2.0.0-alpha.2", ("<2.0.0-alpha.1",)),
            id="prerelease-bound-compares-identifiers",
        ),
        pytest.param(
            (">=1.0.0, <2.0.0-alpha.beta", "2.0.0", ("<2.0.0-alpha.beta",)),
            id="prerelease-bound-with-dot",
        ),
        pytest.param(
            (">=1.0.0, <2.0.0-rc.1+build", "2.0.0", ("<2.0.0-rc.1+build",)),
            id="prerelease-bound-with-build",
        ),
        pytest.param(
            (">=1.0.0, <2.0.0-01", "2.0.0", ()), id="leading-zero-prerelease-ignored"
        ),
        pytest.param(
            (">=1.0.0, <2.0-beta", "2.0.0", ()), id="partial-prerelease-ignored"
        ),
        pytest.param(
            (">=1.0.0, <18446744073709551616.0.0", "2.0.0", ()),
            id="u64-overflow-ignored",
        ),
        pytest.param((">=1.0.0, <abc", "2.0.0", ()), id="unparseable-bound-ignored"),
        pytest.param((">=1.0.0, junk", "2.0.0", ()), id="non-comparator-ignored"),
        pytest.param((">=1.0.0", "2.0.0", ()), id="single-clause-has-no-retained"),
        pytest.param(("1.0.0", "2.0.0", ()), id="bare-requirement-has-no-retained"),
        pytest.param((">=1.0.0, <2", "abc", ()), id="unparseable-target-ignored"),
        pytest.param((">=1.0.0, <2", "2.0", ()), id="partial-target-ignored"),
        pytest.param((">=1.0.0, <2", "2.0.*", ()), id="wildcard-target-ignored"),
        pytest.param(
            (">=1.0.0, <2.0.0-alpha", "2.0.0-alpha.1", ("<2.0.0-alpha",)),
            id="prerelease-prefix-rejects",
        ),
        pytest.param(
            (">=1.0.0, <2.0.0-10", "2.0.0-9", ()),
            id="numeric-prerelease-length-accepts",
        ),
        pytest.param(
            (">=1.0.0, <2.0.0-beta", "2.0.0-alpha", ()),
            id="alphabetic-prerelease-accepts",
        ),
    ],
)
def test_retained_bounds_report_only_excluded_clauses(
    case: tuple[str, str, tuple[str, ...]],
) -> None:
    """Only understood retained comparators that reject the target are named."""
    requirement, target, expected = case

    excluded = bump_manifest_rewrite_bounds.excluding_retained_bounds(
        requirement, target
    )

    assert excluded == expected, (
        "retained-bound analysis must report exactly the excluding clauses"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("<0.7", "0.6.0", ("<0.6.0",)), id="strict-upper-excludes"),
        pytest.param((">1.0.0", "2.0.0", (">2.0.0",)), id="strict-lower-excludes"),
        pytest.param((">=1.0.0", "2.0.0", ()), id="inclusive-lower-retained"),
        pytest.param(("=1.0.0", "2.0.0", ()), id="equality-retained"),
        pytest.param(("^1.0.0", "2.0.0", ()), id="caret-rewritten"),
        pytest.param(("~1.0.0", "2.0.0", ()), id="tilde-rewritten"),
        pytest.param(("<0.7", "0.6", ()), id="partial-target-ignored"),
        pytest.param(("<0.7", "not-a-version", ()), id="unparseable-target-ignored"),
        pytest.param(("<0.7-beta", "0.6.0", ("<0.6.0",)), id="prerelease-first-bound"),
    ],
)
def test_rewritten_first_bound_reports_only_strict_operators(
    case: tuple[str, str, tuple[str, ...]],
) -> None:
    """A rewritten strict comparator excludes its own target version."""
    requirement, target, expected = case

    excluded = bump_manifest_rewrite_bounds.excluding_rewritten_first_bound(
        requirement, target
    )

    assert excluded == expected, (
        "rewritten-first-bound analysis must only name strict operators"
    )


def test_requirement_bound_warning_combines_both_analyses() -> None:
    """A rewritten strict operator is reported with retained exclusions."""
    warning = bump_manifest_rewrite_bounds.requirement_bound_warning(
        "<2.0.0, >=4.0.0", "3.0.0"
    )

    assert warning is not None, "excluding comparators must produce warning data"
    assert warning.requirement == "<2.0.0, >=4.0.0", (
        "the warning must carry the original requirement text"
    )
    assert warning.target_version == "3.0.0", (
        "the warning must carry the requested target version"
    )
    assert warning.excluded_bounds == ("<3.0.0", ">=4.0.0"), (
        "the warning must list the rewritten and retained excluding bounds in order"
    )


def test_requirement_bound_warning_is_none_for_compatible_bounds() -> None:
    """A requirement every understood bound accepts produces no warning."""
    warning = bump_manifest_rewrite_bounds.requirement_bound_warning(
        ">=0.6.0-beta4, <0.7", "0.6.0"
    )

    assert warning is None, "compatible comparators must not produce warning data"


def test_format_requirement_bound_warning_names_target_and_bounds(
    snapshot: SnapshotAssertion,
) -> None:
    """The operator-facing message names the target version and each bound."""
    warning = bump_manifest_rewrite_bounds.RequirementBoundWarning(
        requirement=">=1.0.0, <2",
        target_version="2.0.0",
        excluded_bounds=("<2", ">3"),
    )

    message = bump_manifest_rewrite_bounds.format_requirement_bound_warning(warning)

    assert message == snapshot(name="requirement_bound_warning"), (
        "the operator-facing warning must pin its full wording"
    )
