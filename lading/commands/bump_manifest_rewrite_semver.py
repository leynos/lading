"""Track SemVer suffixes while matching configured path templates."""

import bisect
import re
import string
import typing as typ

_SEMVER_COMPONENT_SUFFIXES: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9.])\d+\.\d+\.\d+"
    r"(?P<prerelease>-(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)?"
    r"(?P<build>\+(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)?"
)


def component_ranges(value: str) -> tuple[tuple[int, int], ...]:
    """Return prerelease and build-metadata ranges in a source value."""
    ranges: list[tuple[int, int]] = []
    for match in _SEMVER_COMPONENT_SUFFIXES.finditer(value):
        for component in ("prerelease", "build"):
            start, end = match.span(component)
            if start >= 0:
                ranges.append((start, end))
    return tuple(ranges)


def is_component_match(
    match_start: int,
    protected_ranges: tuple[tuple[int, int], ...],
    range_starts: tuple[int, ...],
) -> bool:
    """Check a version-field start against precomputed SemVer suffix ranges."""
    range_index = bisect.bisect_right(range_starts, match_start) - 1
    return range_index >= 0 and match_start < protected_ranges[range_index][1]


def version_placeholder_offsets(
    template: str,
    crate_name: str,
    version: str,
) -> tuple[int, ...]:
    """Find rendered offsets of version fields in a validated template."""
    cursor = 0
    offsets: list[int] = []
    for literal, field, _format_spec, _conversion in string.Formatter().parse(template):
        cursor += len(literal)
        match field:
            case "crate":
                cursor += len(crate_name)
            case "version":
                offsets.append(cursor)
                cursor += len(version)
            case _:
                pass
    return tuple(offsets)
