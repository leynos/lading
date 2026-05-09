"""Index-missing-version detection helpers for cargo package/publish errors."""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

_INDEX_MISSING_VERSION_MARKERS: tuple[str, ...] = (
    "failed to select a version for the requirement",
    "location searched: crates.io index",
)

# Capture the dependency crate name from cargo's index-lookup error, e.g.
#   failed to select a version for the requirement `inner_crate = "^0.8.0"`
_INDEX_MISSING_VERSION_NAME_PATTERN = re.compile(
    r"failed to select a version for the requirement [`'\"]"
    r"(?P<name>[A-Za-z0-9_][A-Za-z0-9_-]*)\s*=",
    re.IGNORECASE,
)


def _is_index_missing_version_error(exit_code: int, stdout: str, stderr: str) -> bool:
    """Return True when ``cargo package`` failed due to an unindexed dependency.

    The cargo command exits non-zero with output that simultaneously mentions
    the version selection failure and the crates.io index. Both markers are
    required to minimize false positives from unrelated lookup failures.
    """
    if exit_code == 0:
        return False
    haystack = f"{stdout}\n{stderr}".lower()
    return all(marker in haystack for marker in _INDEX_MISSING_VERSION_MARKERS)


def _extract_missing_dependency_name(stdout: str, stderr: str) -> str | None:
    """Return the missing dependency crate name parsed from cargo output."""
    for stream in (stderr, stdout):
        match = _INDEX_MISSING_VERSION_NAME_PATTERN.search(stream)
        if match is not None:
            return match.group("name")
    return None


@dc.dataclass(frozen=True, slots=True)
class _CargoInvocation:
    """Identifies a cargo invocation that produced an index-lookup failure."""

    crate_name: str
    subcommand: typ.Literal["package", "publish"]
    output: tuple[int, str, str]
