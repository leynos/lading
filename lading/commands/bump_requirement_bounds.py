"""Find Cargo comparators that exclude a bump target.

:func:`excluding_rewritten_first_bound` checks a strict first comparator after
its version is replaced with the target. :func:`excluding_retained_bounds`
checks later comparators that remain verbatim. :func:`requirement_bound_warning`
combines those pure checks into warning data for command layers to log without
changing the user's comparator operators.

Examples
--------
>>> excluding_retained_bounds(">=0.6.0, <0.6", "0.6.0")
('<0.6',)
"""

import dataclasses as dc
import re
import typing as typ

_VERSION_PATTERN: typ.Final[re.Pattern[str]] = re.compile(
    r"(?P<core>(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2})"
    r"(?P<wildcard>\.(?:\*|x|X))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
_COMPARATOR_PATTERN: typ.Final[re.Pattern[str]] = re.compile(
    r"\s*(?P<operator><=|>=|<|>|=)\s*(?P<version>\S+)\s*"
)
_VERSION_COMPONENT_COUNT: typ.Final[int] = 3
_MAX_U64_TEXT: typ.Final[str] = "18446744073709551615"


@dc.dataclass(frozen=True, slots=True)
class RequirementBoundWarning:
    """Data describing Cargo comparators that reject a bump target.

    Attributes
    ----------
    requirement : str
        Original Cargo requirement being rewritten.
    target_version : str
        Requested version that one or more comparators reject.
    excluded_bounds : tuple[str, ...]
        Comparator text retained or rewritten by the bump that excludes target.
    """

    requirement: str
    target_version: str
    excluded_bounds: tuple[str, ...]


def requirement_bound_warning(
    requirement: str,
    target_version: str,
) -> RequirementBoundWarning | None:
    """Return warning data when a rewritten or retained bound rejects target.

    Parameters
    ----------
    requirement : str
        Cargo requirement whose first version is replaced and later clauses
        are preserved.
    target_version : str
        Full three-component semantic version being applied by the bump.

    Returns
    -------
    RequirementBoundWarning or None
        Requirement and comparator details for an excluding bound, or ``None``
        when all understood bounds include the target.

    Examples
    --------
    >>> warning = requirement_bound_warning(
    ...     ">=1.0.0, <2", "2.0.0"
    ... )
    >>> warning.excluded_bounds
    ('<2',)
    >>> requirement_bound_warning(">=0.6.0-beta4, <0.7", "0.6.0") is None
    True
    """
    excluded_bounds = (
        *excluding_rewritten_first_bound(requirement, target_version),
        *excluding_retained_bounds(requirement, target_version),
    )
    if not excluded_bounds:
        return None
    return RequirementBoundWarning(requirement, target_version, excluded_bounds)


def format_requirement_bound_warning(warning: RequirementBoundWarning) -> str:
    """Format returned comparator data for a command-layer warning log.

    Parameters
    ----------
    warning : RequirementBoundWarning
        Structured comparator warning produced by
        :func:`requirement_bound_warning`.

    Returns
    -------
    str
        Operator-facing warning text.
    """
    return (
        f"Target version {warning.target_version} does not satisfy Cargo "
        f"comparator(s) {', '.join(warning.excluded_bounds)} in requirement "
        f"{warning.requirement!r}; the rewritten requirement may not resolve"
    )


class _Version(typ.NamedTuple):
    """Parsed version components used for comparator checks."""

    core: tuple[int, int, int]
    precision: int
    prerelease: tuple[tuple[bool, str], ...]


def excluding_retained_bounds(
    requirement: str,
    target_version: str,
) -> tuple[str, ...]:
    """Return understood retained Cargo comparators that reject the target.

    Only comparator clauses after the first requirement clause are considered.
    Unsupported clauses and an unparseable target version are ignored.

    Parameters
    ----------
    requirement : str
        Cargo version requirement whose later comparator clauses are retained.
    target_version : str
        Full three-component semantic version being applied by the bump.

    Returns
    -------
    tuple[str, ...]
        Retained comparator clauses understood to reject ``target_version``,
        in their original order. Returns an empty tuple when no understood
        retained comparator rejects the target.

    Examples
    --------
    >>> excluding_retained_bounds(">=0.6.0, <0.6", "0.6.0")
    ('<0.6',)
    >>> excluding_retained_bounds(">=0.6.0, <1.0", "0.6.0")
    ()
    """
    _, separator, remaining = requirement.partition(",")
    if not separator:
        return ()
    target = _parse_version(target_version, require_full=True)
    if target is None:
        return ()

    excluded: list[str] = []
    for constraint in remaining.split(","):
        match = _COMPARATOR_PATTERN.fullmatch(constraint)
        if match is None:
            continue
        bound = _parse_version(match.group("version"), require_full=False)
        if bound is None:
            continue
        if not _satisfies(target, match.group("operator"), bound):
            excluded.append(constraint.strip())
    return tuple(excluded)


def excluding_rewritten_first_bound(
    requirement: str,
    target_version: str,
) -> tuple[str, ...]:
    """Return a rewritten strict first comparator that rejects the target.

    The bump operation replaces the first comparator's version but retains its
    operator. A strict ``<`` or ``>`` comparator therefore excludes the target
    after rewriting. Inclusive, equality, caret, and tilde comparators do not.

    Parameters
    ----------
    requirement : str
        Cargo requirement whose first comparator will be rewritten.
    target_version : str
        Full three-component semantic version being applied by the bump.

    Returns
    -------
    tuple[str, ...]
        The rewritten strict comparator when it excludes ``target_version``;
        otherwise an empty tuple.

    Examples
    --------
    >>> excluding_rewritten_first_bound("<0.7", "0.6.0")
    ('<0.6.0',)
    >>> excluding_rewritten_first_bound(">=0.6.0-beta4", "0.6.0")
    ()
    """
    target = _parse_version(target_version, require_full=True)
    if target is None:
        return ()

    first_constraint = requirement.partition(",")[0]
    match = _COMPARATOR_PATTERN.fullmatch(first_constraint)
    if match is None:
        return ()
    operator = match.group("operator")
    if operator not in {"<", ">"}:
        return ()
    return (f"{operator}{target_version}",)


def _parse_version(value: str, *, require_full: bool) -> _Version | None:
    """Parse a SemVer core and prerelease for a supported Cargo bound."""
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        return None
    core_text = match.group("core")
    core_components = core_text.split(".")
    if any(not _is_u64_component(part) for part in core_components):
        return None
    has_wildcard = match.group("wildcard") is not None
    if has_wildcard and _has_invalid_wildcard_precision(
        require_full=require_full,
        core_component_count=len(core_components),
        prerelease_text=match.group("prerelease"),
        build_text=match.group("build"),
    ):
        return None
    core_parts = tuple(int(part) for part in core_components)
    precision = len(core_parts)
    prerelease_text = match.group("prerelease")
    if _has_invalid_precision(precision, prerelease_text, require_full=require_full):
        return None
    prerelease = _parse_prerelease(prerelease_text)
    if prerelease is None:
        return None
    return _Version(_pad_core(core_parts), precision, prerelease)


def _is_u64_component(value: str) -> bool:
    """Check a decimal version component against Cargo's u64 range."""
    return len(value) < len(_MAX_U64_TEXT) or (
        len(value) == len(_MAX_U64_TEXT) and value <= _MAX_U64_TEXT
    )


def _has_invalid_wildcard_precision(
    *,
    require_full: bool,
    core_component_count: int,
    prerelease_text: str | None,
    build_text: str | None,
) -> bool:
    """Reject wildcard comparators outside Cargo's supported form."""
    if require_full:
        return True
    if core_component_count == _VERSION_COMPONENT_COUNT:
        return True
    if prerelease_text is not None:
        return True
    return build_text is not None


def _has_invalid_precision(
    precision: int, prerelease_text: str | None, *, require_full: bool
) -> bool:
    """Check core precision constraints for target and comparator versions."""
    if require_full and precision != _VERSION_COMPONENT_COUNT:
        return True
    return bool(prerelease_text) and precision != _VERSION_COMPONENT_COUNT


def _parse_prerelease(
    value: str | None,
) -> tuple[tuple[bool, str], ...] | None:
    """Parse prerelease identifiers, rejecting numeric leading zeroes."""
    if not value:
        return ()
    identifiers = value.split(".")
    if any(_has_numeric_leading_zero(identifier) for identifier in identifiers):
        return None
    return tuple((part.isdigit(), part) for part in identifiers)


def _has_numeric_leading_zero(identifier: str) -> bool:
    """Return whether a numeric prerelease identifier has a leading zero."""
    return identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")


def _pad_core(parts: tuple[int, ...]) -> tuple[int, int, int]:
    """Pad a partial comparator core to three components."""
    padded = (*parts, *(0 for _ in range(_VERSION_COMPONENT_COUNT - len(parts))))
    return (padded[0], padded[1], padded[2])


def _satisfies(target: _Version, operator: str, bound: _Version) -> bool:
    """Apply a Cargo comparator, including Cargo's partial-version ranges."""
    match operator:
        case "<":
            return _matches_strict(bound, target, operator="<")
        case "<=":
            return _matches_exact(bound, target) or _matches_strict(
                bound, target, operator="<"
            )
        case ">":
            return _matches_strict(bound, target, operator=">")
        case ">=":
            return _matches_exact(bound, target) or _matches_strict(
                bound, target, operator=">"
            )
        case "=":
            return _matches_exact(bound, target)
        case _:
            return True


def _matches_exact(bound: _Version, target: _Version) -> bool:
    """Return whether all comparator components match the target prefix."""
    return (
        target.core[: bound.precision] == bound.core[: bound.precision]
        and target.prerelease == bound.prerelease
    )


def _matches_strict(bound: _Version, target: _Version, *, operator: str) -> bool:
    """Apply Cargo's strict less-than or greater-than partial comparator."""
    is_less = operator == "<"
    for index in range(bound.precision):
        if target.core[index] != bound.core[index]:
            return (
                target.core[index] < bound.core[index]
                if is_less
                else target.core[index] > bound.core[index]
            )
    if bound.precision != _VERSION_COMPONENT_COUNT:
        return False
    comparison = _compare_prerelease(target.prerelease, bound.prerelease)
    return comparison < 0 if is_less else comparison > 0


def _compare_prerelease(
    left: tuple[tuple[bool, str], ...], right: tuple[tuple[bool, str], ...]
) -> int:
    """Compare SemVer prereleases; an empty prerelease sorts after all others."""
    if not left or not right:
        return (not left) - (not right)
    for (left_is_numeric, left_part), (right_is_numeric, right_part) in zip(
        left, right, strict=False
    ):
        if left_is_numeric == right_is_numeric and left_part == right_part:
            continue
        if left_is_numeric != right_is_numeric:
            return -1 if left_is_numeric else 1
        if left_is_numeric:
            comparison = (len(left_part) > len(right_part)) - (
                len(left_part) < len(right_part)
            )
            if comparison == 0:
                comparison = (left_part > right_part) - (left_part < right_part)
        else:
            comparison = (left_part > right_part) - (left_part < right_part)
        return comparison
    return (len(left) > len(right)) - (len(left) < len(right))
