"""Cyclopts argument declarations for the :mod:`lading` CLI.

Extracted from :mod:`lading.cli` (issue #108) so option declarations live
apart from dispatch logic. ``cli`` re-imports every public name, so external
access through ``lading.cli`` keeps working.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ
from pathlib import Path

from cyclopts import Parameter

WORKSPACE_ROOT_ENV_VAR = "LADING_WORKSPACE_ROOT"
WORKSPACE_ROOT_REQUIRED_MESSAGE = "--workspace-root requires a value"
WORKSPACE_PARAMETER = Parameter(
    name="workspace-root",
    env_var=WORKSPACE_ROOT_ENV_VAR,
    help="Path to the Rust workspace root.",
)

_VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _validate_version_argument(_hint: object, version: str) -> None:
    """Reject a non-semantic-version ``version`` via cyclopts' validator hook."""
    # Cyclopts calls validators as ``validator(type_hint, value)`` and converts a
    # raised ValueError into a formatted ValidationError, so a bad version is
    # reported through cyclopts' own error flow rather than a bare SystemExit.
    if not _VERSION_PATTERN.fullmatch(version):
        message = (
            "Invalid version argument "
            f"{version!r}. Expected semantic version in the form "
            "<major>.<minor>.<patch> with optional pre-release/build segments."
        )
        raise ValueError(message)


VERSION_PARAMETER = Parameter(
    validator=_validate_version_argument,
    help="Target semantic version (e.g., 1.2.3) to set across workspace manifests.",
)

DRY_RUN_PARAMETER = Parameter(
    name="dry-run",
    help="Preview manifest changes without writing files.",
)

REBUILD_LOCKFILES_PARAMETER = Parameter(
    name="rebuild-lockfiles",
    negative="no-rebuild-lockfiles",
    help="Regenerate Cargo.lock files after manifest updates.",
)

LIVE_PARAMETER = Parameter(
    name="live",
    help="Run cargo publish without --dry-run; default behaviour is dry-run.",
)

FORBID_DIRTY_PARAMETER = Parameter(
    name="forbid-dirty",
    help=("Require a clean working tree before running publish pre-flight checks."),
)

ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER = Parameter(
    name="allow-unpublished-workspace-deps",
    help=(
        "Dry-run only: downgrade cargo package failures caused by a sibling "
        "workspace crate version not yet on crates.io to a warning when the "
        "missing crate is part of the planned publish set and appears earlier "
        "in publish order. Defaults to enabled in dry-run mode. Cannot be "
        "combined with --live."
    ),
)

SCCACHE_STATS_ENV_VAR = "LADING_SCCACHE_STATS"
SCCACHE_STATS_PARAMETER = Parameter(
    name="sccache-stats",
    env_var=SCCACHE_STATS_ENV_VAR,
    help=(
        "Query the sccache binary named by RUSTC_WRAPPER before the first "
        "cargo build and after every cargo package/publish invocation, and "
        "log one compiler-cache summary line per invocation (two per crate "
        "in a dry run). Skipped with a warning when RUSTC_WRAPPER does not "
        "name sccache."
    ),
)

SCCACHE_STATS_JSON_ENV_VAR = "LADING_SCCACHE_STATS_JSON"
SCCACHE_STATS_JSON_PARAMETER = Parameter(
    name="sccache-stats-json",
    env_var=SCCACHE_STATS_JSON_ENV_VAR,
    help=(
        "Write the compiler-cache statistics collected by --sccache-stats to "
        "this JSON file; implies --sccache-stats."
    ),
)


@dc.dataclass(frozen=True, slots=True)
class PublishFlags:
    """The ``lading publish`` flags, flattened onto the command line by Cyclopts.

    Grouping the flags in one dataclass keeps the command's signature to the
    workspace root plus this bundle while every field still surfaces as its
    own option, with its help text, negative form, and environment default.

    Examples
    --------
    >>> PublishFlags().live
    False
    >>> PublishFlags(sccache_stats_json=Path("stats.json")).sccache_stats_json
    PosixPath('stats.json')
    """

    forbid_dirty: typ.Annotated[bool, FORBID_DIRTY_PARAMETER] = False
    live: typ.Annotated[bool, LIVE_PARAMETER] = False
    allow_unpublished_workspace_deps: typ.Annotated[
        bool | None, ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER
    ] = None
    sccache_stats: typ.Annotated[bool, SCCACHE_STATS_PARAMETER] = False
    sccache_stats_json: typ.Annotated[Path | None, SCCACHE_STATS_JSON_PARAMETER] = None


# These aliases remain public for integrations that import CLI annotations.
# The CLI signatures deliberately spell out Annotated metadata inline because
# Cyclopts evaluates those annotations at runtime and does not unwrap PEP 695
# aliases when constructing its option parser.
type WorkspaceRootOption = typ.Annotated[Path, WORKSPACE_PARAMETER]
type VersionArgument = typ.Annotated[str, VERSION_PARAMETER]
type DryRunFlag = typ.Annotated[bool, DRY_RUN_PARAMETER]
type RebuildLockfilesFlag = typ.Annotated[bool, REBUILD_LOCKFILES_PARAMETER]
type LiveFlag = typ.Annotated[bool, LIVE_PARAMETER]
type ForbidDirtyFlag = typ.Annotated[bool, FORBID_DIRTY_PARAMETER]
type AllowUnpublishedWorkspaceDepsFlag = typ.Annotated[
    bool | None, ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER
]
type SccacheStatsFlag = typ.Annotated[bool, SCCACHE_STATS_PARAMETER]
type SccacheStatsJsonOption = typ.Annotated[Path | None, SCCACHE_STATS_JSON_PARAMETER]


__all__ = [
    "ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER",
    "DRY_RUN_PARAMETER",
    "FORBID_DIRTY_PARAMETER",
    "LIVE_PARAMETER",
    "REBUILD_LOCKFILES_PARAMETER",
    "SCCACHE_STATS_ENV_VAR",
    "SCCACHE_STATS_JSON_ENV_VAR",
    "SCCACHE_STATS_JSON_PARAMETER",
    "SCCACHE_STATS_PARAMETER",
    "VERSION_PARAMETER",
    "WORKSPACE_PARAMETER",
    "WORKSPACE_ROOT_ENV_VAR",
    "WORKSPACE_ROOT_REQUIRED_MESSAGE",
    "AllowUnpublishedWorkspaceDepsFlag",
    "DryRunFlag",
    "ForbidDirtyFlag",
    "LiveFlag",
    "PublishFlags",
    "RebuildLockfilesFlag",
    "SccacheStatsFlag",
    "SccacheStatsJsonOption",
    "VersionArgument",
    "WorkspaceRootOption",
]
