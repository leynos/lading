"""Adapt cargo subprocess output into structured command failures.

Cargo emits registry and resolver failures as process output. Higher-level
publish logic should not need to know the exact stderr/stdout markers that
identify one diagnostic shape, so this module owns that parsing boundary and
returns typed value objects instead.

Callers pass the crate name, cargo subcommand, and raw subprocess result to
``parse_index_lookup_failure``. When the output matches Cargo's crates.io
index-lookup diagnostic, the function returns
``CargoIndexLookupFailure`` with the original process streams and the extracted
missing dependency name. Non-matching or successful invocations return
``None``.

Example
-------
```python
from lading.commands.cargo_output_adapter import parse_index_lookup_failure

failure = parse_index_lookup_failure(
    crate_name="beta",
    subcommand="package",
    result=CargoSubprocessResult(
        exit_code=101,
        stdout="",
        stderr=cargo_stderr,
    ),
)
if failure is not None:
    handle_index_lookup_failure(failure)
```
"""

from __future__ import annotations

import dataclasses as dc
import logging
import re
import typing as typ

LOGGER = logging.getLogger(__name__)

_INDEX_MISSING_VERSION_MARKERS: tuple[str, ...] = (
    "failed to select a version for the requirement",
    "location searched: crates.io index",
)

# Capture the dependency crate name from cargo's index-lookup error, e.g.
#   failed to select a version for the requirement `inner_crate = "^0.8.0"`
_INDEX_MISSING_VERSION_NAME_PATTERN = re.compile(
    "failed to select a version for the requirement [`'\"]"  # noqa: RUF039 - keeps escaped quote pattern for cargo diagnostics
    r"(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*=",
    re.IGNORECASE,
)


@dc.dataclass(frozen=True, slots=True)
class CargoSubprocessResult:
    """Raw process output from a single cargo invocation.

    Attributes
    ----------
    exit_code : int
        Process exit code returned by the cargo subprocess.
    stdout : str
        Standard output stream captured from the cargo subprocess.
    stderr : str
        Standard error stream captured from the cargo subprocess.
    """

    exit_code: int
    stdout: str
    stderr: str


@dc.dataclass(frozen=True, slots=True)
class CargoIndexLookupFailure:
    """Represents a cargo failure where the index could not resolve a dependency.

    Attributes
    ----------
    crate_name : str
        Name of the crate whose cargo invocation failed.
    subcommand : Literal["package", "publish"]
        Cargo subcommand that produced the failure.
    exit_code : int
        Non-zero process exit code from the failed cargo subprocess.
    stdout : str
        Standard output stream captured from the failed subprocess.
    stderr : str
        Standard error stream captured from the failed subprocess.
    missing_dependency_name : str | None
        Name of the dependency crate that could not be resolved from the
        crates.io index, or :data:`None` if name extraction failed.
    """

    crate_name: str
    subcommand: typ.Literal["package", "publish"]
    exit_code: int
    stdout: str
    stderr: str
    missing_dependency_name: str | None


def parse_index_lookup_failure(
    *,
    crate_name: str,
    subcommand: typ.Literal["package", "publish"],
    result: CargoSubprocessResult,
) -> CargoIndexLookupFailure | None:
    r"""Return a structured index-lookup failure parsed from cargo output.

    Parameters
    ----------
    crate_name:
        Name of the crate whose cargo invocation produced ``stdout`` and
        ``stderr``.
    subcommand:
        Cargo subcommand that produced the output. Currently limited to the
        publish workflow's ``package`` and ``publish`` phases.
    result:
        Raw process output from the cargo invocation.

    Returns
    -------
    CargoIndexLookupFailure | None
        A structured failure when cargo could not resolve a dependency from
        the crates.io index, otherwise :data:`None`.

    Examples
    --------
    >>> cargo_stderr = (
    ...     "error: failed to select a version for the requirement "
    ...     '`inner_crate = "^0.8.0"`\n'
    ...     "location searched: crates.io index"
    ... )
    >>> result = CargoSubprocessResult(
    ...     exit_code=101,
    ...     stdout="",
    ...     stderr=cargo_stderr,
    ... )
    >>> failure = parse_index_lookup_failure(
    ...     crate_name="beta",
    ...     subcommand="package",
    ...     result=result,
    ... )
    >>> failure.missing_dependency_name
    'inner_crate'
    """
    if result.exit_code == 0:
        return None

    haystack = f"{result.stdout}\n{result.stderr}"
    if not _output_matches_index_markers(haystack):
        LOGGER.debug(
            "cargo %s for crate %s exited %d without index-lookup markers; "
            "not classifying as an index-miss failure",
            subcommand,
            crate_name,
            result.exit_code,
        )
        return None

    return _build_index_lookup_failure(
        crate_name=crate_name,
        subcommand=subcommand,
        result=result,
    )


def _output_matches_index_markers(haystack: str) -> bool:
    """Return whether both crates.io index-miss markers appear in cargo output."""
    return all(
        re.search(re.escape(marker), haystack, re.IGNORECASE)
        for marker in _INDEX_MISSING_VERSION_MARKERS
    )


def _build_index_lookup_failure(
    *,
    crate_name: str,
    subcommand: typ.Literal["package", "publish"],
    result: CargoSubprocessResult,
) -> CargoIndexLookupFailure:
    """Assemble the structured failure once cargo output matched the markers."""
    missing_dependency_name = _extract_missing_dependency_name(
        result.stdout, result.stderr
    )
    LOGGER.debug(
        "cargo %s for crate %s classified as an index-lookup failure "
        "(missing dependency: %s)",
        subcommand,
        crate_name,
        missing_dependency_name
        if missing_dependency_name is not None
        else "<unparsed>",
    )
    return CargoIndexLookupFailure(
        crate_name=crate_name,
        subcommand=subcommand,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        missing_dependency_name=missing_dependency_name,
    )


def _extract_missing_dependency_name(stdout: str, stderr: str) -> str | None:
    """Return the missing dependency crate name parsed from cargo output."""
    # Cargo writes primary diagnostics to stderr. If both streams happen to
    # match _INDEX_MISSING_VERSION_NAME_PATTERN, prefer the stderr name as the
    # most relevant failure detail and leave conflicting stdout as secondary.
    for stream in (stderr, stdout):
        match = _INDEX_MISSING_VERSION_NAME_PATTERN.search(stream)
        if match is not None:
            return match.group("name")
    return None
