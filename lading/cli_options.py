"""Cyclopts argument declarations for the :mod:`lading` CLI.

Extracted from :mod:`lading.cli` (issue #108) so option declarations live
apart from dispatch logic. ``cli`` re-imports every public name, so external
access through ``lading.cli`` keeps working.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

from cyclopts import Parameter

WORKSPACE_ROOT_ENV_VAR = "LADING_WORKSPACE_ROOT"
WORKSPACE_ROOT_REQUIRED_MESSAGE = "--workspace-root requires a value"
_WORKSPACE_PARAMETER = Parameter(
    name="workspace-root",
    env_var=WORKSPACE_ROOT_ENV_VAR,
    help="Path to the Rust workspace root.",
)
WorkspaceRootOption = typ.Annotated[Path, _WORKSPACE_PARAMETER]

_VERSION_PARAMETER = Parameter(
    help="Target semantic version (e.g., 1.2.3) to set across workspace manifests.",
)
VersionArgument = typ.Annotated[str, _VERSION_PARAMETER]

_DRY_RUN_PARAMETER = Parameter(
    name="dry-run",
    help="Preview manifest changes without writing files.",
)
DryRunFlag = typ.Annotated[bool, _DRY_RUN_PARAMETER]

_REBUILD_LOCKFILES_PARAMETER = Parameter(
    name="rebuild-lockfiles",
    negative="no-rebuild-lockfiles",
    help="Regenerate Cargo.lock files after manifest updates.",
)
RebuildLockfilesFlag = typ.Annotated[bool, _REBUILD_LOCKFILES_PARAMETER]

_LIVE_PARAMETER = Parameter(
    name="live",
    help="Run cargo publish without --dry-run; default behaviour is dry-run.",
)
LiveFlag = typ.Annotated[bool, _LIVE_PARAMETER]

_FORBID_DIRTY_PARAMETER = Parameter(
    name="forbid-dirty",
    help=("Require a clean working tree before running publish pre-flight checks."),
)
ForbidDirtyFlag = typ.Annotated[bool, _FORBID_DIRTY_PARAMETER]

_ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER = Parameter(
    name="allow-unpublished-workspace-deps",
    help=(
        "Dry-run only: downgrade cargo package failures caused by a sibling "
        "workspace crate version not yet on crates.io to a warning when the "
        "missing crate is part of the planned publish set and appears earlier "
        "in publish order. Defaults to enabled in dry-run mode. Cannot be "
        "combined with --live."
    ),
)
AllowUnpublishedWorkspaceDepsFlag = typ.Annotated[
    bool | None, _ALLOW_UNPUBLISHED_WORKSPACE_DEPS_PARAMETER
]


_VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _validate_version_argument(version: str) -> None:
    """Ensure ``version`` matches the semantic version pattern."""
    if not _VERSION_PATTERN.fullmatch(version):
        message = (
            "Invalid version argument "
            f"{version!r}. Expected semantic version in the form "
            "<major>.<minor>.<patch> with optional pre-release/build segments."
        )
        raise SystemExit(message)


__all__ = [
    "WORKSPACE_ROOT_ENV_VAR",
    "WORKSPACE_ROOT_REQUIRED_MESSAGE",
    "AllowUnpublishedWorkspaceDepsFlag",
    "DryRunFlag",
    "ForbidDirtyFlag",
    "LiveFlag",
    "RebuildLockfilesFlag",
    "VersionArgument",
    "WorkspaceRootOption",
    "_validate_version_argument",
]
