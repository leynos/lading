"""Check whether retained Cargo comparators exclude a target version."""

import re
import typing as typ

_VERSION_PATTERN: typ.Final[re.Pattern[str]] = re.compile(
    r"(?P<core>(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){0,2})"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_COMPARATOR_PATTERN: typ.Final[re.Pattern[str]] = re.compile(
    r"\s*(?P<operator><=|>=|<|>|=)\s*(?P<version>\S+)\s*"
)
_VERSION_COMPONENT_COUNT: typ.Final[int] = 3


class _Version(typ.NamedTuple):
    """Parsed version components used for comparator checks."""

    core: tuple[int, int, int]
    precision: int
    prerelease: tuple[int | str, ...]


def excluding_retained_bounds(
    requirement: str,
    target_version: str,
) -> tuple[str, ...]:
    """Return understood retained comparators that reject ``target_version``."""
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


def _parse_version(value: str, *, require_full: bool) -> _Version | None:
    """Parse a SemVer core and prerelease for a supported Cargo bound."""
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        return None
    core_parts = tuple(int(part) for part in match.group("core").split("."))
    precision = len(core_parts)
    prerelease_text = match.group("prerelease")
    if require_full and precision != _VERSION_COMPONENT_COUNT:
        return None
    if prerelease_text and precision != _VERSION_COMPONENT_COUNT:
        return None
    prerelease_parts = (prerelease_text or "").split(".")
    if any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in prerelease_parts
    ):
        return None
    padded_core = (
        *core_parts,
        *(0 for _ in range(_VERSION_COMPONENT_COUNT - precision)),
    )
    core = (padded_core[0], padded_core[1], padded_core[2])
    prerelease = (
        tuple(int(part) if part.isdigit() else part for part in prerelease_parts)
        if prerelease_text
        else ()
    )
    return _Version(core, precision, prerelease)


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
    left: tuple[int | str, ...], right: tuple[int | str, ...]
) -> int:
    """Compare SemVer prereleases; an empty prerelease sorts after all others."""
    if not left or not right:
        return (not left) - (not right)
    for left_part, right_part in zip(left, right, strict=False):
        if left_part == right_part:
            continue
        match left_part, right_part:
            case int(), int():
                comparison = (left_part > right_part) - (left_part < right_part)
            case str(), str():
                comparison = (left_part > right_part) - (left_part < right_part)
            case int(), str():
                comparison = -1
            case str(), int():
                comparison = 1
            case _:
                typ.assert_never((left_part, right_part))
        return comparison
    return (len(left) > len(right)) - (len(left) < len(right))
