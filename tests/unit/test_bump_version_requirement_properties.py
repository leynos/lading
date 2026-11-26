"""Property tests for version requirement rewriting."""

import typing as typ

import hypothesis.strategies as st
from hypothesis import given

from lading.commands.bump_requirement_bounds import excluding_retained_bounds
from lading.commands.bump_toml import compose_requirement


class _Version(typ.NamedTuple):
    """Generated semantic version parts used by the independent oracle."""

    core: tuple[int, int, int]
    precision: int
    prerelease: tuple[int | str, ...]
    text: str


_ComparatorOperator = typ.Literal["<", "<=", ">", ">=", "="]

_COMPONENT = st.integers(min_value=0, max_value=20)
_IDENTIFIER = st.one_of(
    st.integers(min_value=0, max_value=9).map(str),
    st.sampled_from(("alpha", "beta", "rc", "z")),
)
_BUILD = st.sampled_from(("", "+build.1", "+ci.4"))


@st.composite
def _full_version(draw: st.DrawFn) -> _Version:
    """Generate a bounded full version with optional prerelease metadata."""
    core = draw(st.tuples(_COMPONENT, _COMPONENT, _COMPONENT))
    identifiers = draw(
        st.one_of(st.just(()), st.lists(_IDENTIFIER, min_size=1, max_size=3))
    )
    prerelease = tuple(int(part) if part.isdigit() else part for part in identifiers)
    prerelease_text = f"-{'.'.join(identifiers)}" if identifiers else ""
    build = draw(_BUILD)
    text = f"{'.'.join(map(str, core))}{prerelease_text}{build}"
    return _Version(core, 3, prerelease, text)


@st.composite
def _bound_version(draw: st.DrawFn) -> _Version:
    """Generate a Cargo comparator version with one to three components."""
    precision = draw(st.integers(min_value=1, max_value=3))
    components = draw(st.lists(_COMPONENT, min_size=precision, max_size=precision))
    padded_core = (
        components[0],
        components[1] if precision > 1 else 0,
        components[2] if precision > 2 else 0,
    )
    identifiers = (
        draw(st.one_of(st.just(()), st.lists(_IDENTIFIER, min_size=1, max_size=3)))
        if precision == 3
        else ()
    )
    prerelease = tuple(int(part) if part.isdigit() else part for part in identifiers)
    prerelease_text = f"-{'.'.join(identifiers)}" if identifiers else ""
    build = draw(_BUILD) if precision == 3 else ""
    text = f"{'.'.join(map(str, components))}{prerelease_text}{build}"
    return _Version(padded_core, precision, prerelease, text)


@st.composite
def _target_with_related_bound(draw: st.DrawFn) -> tuple[_Version, _Version]:
    """Generate equality, same-core prerelease, and adjacent-version pairs."""
    target = draw(_full_version())
    relation = draw(st.sampled_from(("equal", "prerelease", "adjacent")))
    if relation == "equal":
        return target, target
    if relation == "prerelease":
        if target.prerelease:
            return target, _Version(target.core, 3, (), ".".join(map(str, target.core)))
        prerelease = ("alpha",)
        text = f"{'.'.join(map(str, target.core))}-alpha"
        return target, _Version(target.core, 3, prerelease, text)
    adjacent_core = (*target.core[:2], target.core[2] + 1)
    adjacent_text = ".".join(map(str, adjacent_core))
    return target, _Version(adjacent_core, 3, (), adjacent_text)


@st.composite
def _comparator(draw: st.DrawFn) -> tuple[_ComparatorOperator, str, _Version]:
    """Generate a comparator with surrounding whitespace and its oracle data."""
    operator = draw(st.sampled_from(("<", "<=", ">", ">=", "=")))
    version = draw(_bound_version())
    before_operator = draw(st.sampled_from(("", " ", "  ")))
    after_operator = draw(st.sampled_from(("", " ", "  ")))
    trailing = draw(st.sampled_from(("", " ", "  ")))
    return (
        operator,
        f"{before_operator}{operator}{after_operator}{version.text}{trailing}",
        version,
    )


def _compare_prereleases(
    left: tuple[int | str, ...], right: tuple[int | str, ...]
) -> int:
    """Compare SemVer prerelease identifiers without using production code."""
    if not left or not right:
        return (not left) - (not right)
    for left_part, right_part in zip(left, right, strict=False):
        if left_part == right_part:
            continue
        match left_part, right_part:
            case int(), int():
                return (left_part > right_part) - (left_part < right_part)
            case int(), str():
                return -1
            case str(), int():
                return 1
            case str(), str():
                return (left_part > right_part) - (left_part < right_part)
            case _:
                typ.assert_never((left_part, right_part))
    return (len(left) > len(right)) - (len(left) < len(right))


def _compare_target_to_bound(target: _Version, bound: _Version) -> tuple[int, bool]:
    """Return comparison order and equality under a Cargo comparator."""
    target_prefix = target.core[: bound.precision]
    bound_prefix = bound.core[: bound.precision]
    if bound.precision < 3:
        core_comparison = (target_prefix > bound_prefix) - (
            target_prefix < bound_prefix
        )
        return (
            core_comparison,
            core_comparison == 0 and target.prerelease == bound.prerelease,
        )

    core_comparison = (target.core > bound.core) - (target.core < bound.core)
    if core_comparison == 0:
        core_comparison = _compare_prereleases(target.prerelease, bound.prerelease)
    return core_comparison, core_comparison == 0


def _accepts_target(
    target: _Version,
    operator: _ComparatorOperator,
    bound: _Version,
) -> bool:
    """Apply Cargo's generated comparator contract independently."""
    comparison, exact = _compare_target_to_bound(target, bound)
    return {
        "=": exact,
        "<": comparison < 0,
        "<=": comparison < 0 or exact,
        ">": comparison > 0,
        ">=": comparison > 0 or exact,
    }[operator]


@given(
    target=_full_version(),
    comparators=st.lists(_comparator(), min_size=1, max_size=5),
)
def test_excluding_retained_bounds_matches_cargo_reference(
    target: _Version,
    comparators: list[tuple[_ComparatorOperator, str, _Version]],
) -> None:
    """Every understood comparator excludes exactly the reference-rejected target."""
    requirement = ">=0.1.0, " + ", ".join(text for _, text, _ in comparators)
    expected = tuple(
        text.strip()
        for operator, text, bound in comparators
        if not _accepts_target(target, operator, bound)
    )

    actual = excluding_retained_bounds(requirement, target.text)

    assert actual == expected, (
        f"Cargo comparator evaluation for target {target.text!r} should be "
        f"{expected!r}, got {actual!r}"
    )


@given(
    pair=_target_with_related_bound(),
    operator=st.sampled_from(("<", "<=", ">", ">=", "=")),
)
def test_excluding_bounds_handles_versions_related_to_target(
    pair: tuple[_Version, _Version],
    operator: _ComparatorOperator,
) -> None:
    """Related semantic versions exercise equality and prerelease edges."""
    target, bound = pair
    comparator = f"{operator}{bound.text}"
    requirement = f">=0.1.0, {comparator}"
    expected = () if _accepts_target(target, operator, bound) else (comparator,)

    actual = excluding_retained_bounds(requirement, target.text)

    assert actual == expected, (
        f"target {target.text!r} against related bound {comparator!r} should "
        f"produce {expected!r}, got {actual!r}"
    )


_OPERATOR_PREFIX = st.sampled_from(("", "^", "~", "=", ">=", "<=", ">", "<"))
_WHITESPACE = st.sampled_from(("", " ", "  "))
_LATER_CLAUSE = st.tuples(
    _WHITESPACE,
    st.sampled_from(("<", "<=", ">", ">=", "=")),
    _WHITESPACE,
    _full_version().map(lambda version: version.text),
    _WHITESPACE,
).map(lambda parts: "".join(parts))


class _RequirementCase(typ.NamedTuple):
    """Inputs for a generated composite-requirement rewrite."""

    old_version: _Version
    target_version: _Version
    leading: str
    operator: str
    between: str
    trailing: str
    later_clauses: list[str]


@st.composite
def _requirement_case(draw: st.DrawFn) -> _RequirementCase:
    """Generate a requirement prefix, version, whitespace and later clauses."""
    return _RequirementCase(
        old_version=draw(_full_version()),
        target_version=draw(_full_version()),
        leading=draw(_WHITESPACE),
        operator=draw(_OPERATOR_PREFIX),
        between=draw(_WHITESPACE),
        trailing=draw(_WHITESPACE),
        later_clauses=draw(st.lists(_LATER_CLAUSE, max_size=4)),
    )


@given(case=_requirement_case())
def test_compose_requirement_replaces_only_first_version(
    case: _RequirementCase,
) -> None:
    """The first prefix and all whitespace and later comparator text survive."""
    prefix = f"{case.leading}{case.operator}{case.between}"
    suffix = "".join(f",{clause}" for clause in case.later_clauses)
    existing = f"{prefix}{case.old_version.text}{case.trailing}{suffix}"
    expected = f"{prefix}{case.target_version.text}{case.trailing}{suffix}"

    actual = compose_requirement(existing, case.target_version.text)

    assert actual == expected, (
        "only the first requirement version should change; "
        f"expected {expected!r}, got {actual!r}"
    )
