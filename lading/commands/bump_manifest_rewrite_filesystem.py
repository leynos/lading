"""Resolve and read allowlisted manifests for the rewrite planner.

This adapter owns path confinement, glob expansion, member filtering and UTF-8
reads. It returns source records so rewrite policy can run without filesystem
access.
"""

import dataclasses as dc
import glob
import logging
import typing as typ
from itertools import starmap

from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteSource,
)
from lading.utils.confinement import (
    ConfinedPath,
    GlobOptions,
    MatchExpectation,
    UnmatchedPolicy,
    confine_relative_path,
    establish_workspace_root,
    expand_confined_glob,
)
from lading.utils.confinement.errors import BumpPreflightError, MissingBumpTargetError

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from lading.config import ManifestRewriteConfig, StringValueRewriteConfig

_log = logging.getLogger(__name__)

#: Windows rejects a glob metacharacter in a filename with this ``winerror``.
_WINDOWS_INVALID_NAME_WINERROR = 123


@dc.dataclass(slots=True)
class _RewriteSelection:
    """Merged rewrite rules selected for one resolved manifest path."""

    dependencies: bool = False
    string_values: list[StringValueRewriteConfig] = dc.field(default_factory=list)


@dc.dataclass(slots=True)
class _RewriteSelectionContext:
    """Track workspace boundaries and merged rules during path resolution."""

    workspace_root: Path
    skipped_manifests: set[Path]
    selections: dict[Path, _RewriteSelection] = dc.field(default_factory=dict)


def read_manifest_rewrite_sources(
    workspace_root: Path,
    rewrite_groups: cabc.Sequence[ManifestRewriteConfig],
    member_manifest_paths: cabc.Collection[Path],
) -> tuple[ManifestRewriteSource, ...]:
    """Resolve configured non-member manifests and read their original text.

    Parameters
    ----------
    workspace_root : Path
        Root directory against which configured paths are resolved.
    rewrite_groups : Sequence[ManifestRewriteConfig]
        Validated opt-in manifest allowlist and rewrite rules.
    member_manifest_paths : Collection[Path]
        Workspace member manifests, which remain owned by the normal bump
        manifest stage.

    Returns
    -------
    tuple[ManifestRewriteSource, ...]
        Read manifests in configuration and path order, with duplicate paths
        merged and workspace-owned manifests omitted.

    """
    if not rewrite_groups:
        return ()
    root = establish_workspace_root(workspace_root)
    skipped_manifests = {(root / "Cargo.toml").resolve()}
    skipped_manifests.update(path.resolve() for path in member_manifest_paths)
    context = _RewriteSelectionContext(root, skipped_manifests)
    for group in rewrite_groups:
        _select_rewrite_group(context, group)
    return tuple(starmap(_read_source, context.selections.items()))


def _select_rewrite_group(
    context: _RewriteSelectionContext,
    group: ManifestRewriteConfig,
) -> None:
    """Expand every path in a group and merge its rules by resolved path."""
    for configured_path in group.paths:
        _select_configured_path(context, group, configured_path)


def _select_configured_path(
    context: _RewriteSelectionContext,
    group: ManifestRewriteConfig,
    configured_path: str,
) -> None:
    """Validate every file selected by one literal path or glob."""
    candidates = _expand_path(context.workspace_root, configured_path)
    for candidate in candidates:
        _merge_rewrite_candidate(context, group, configured_path, candidate)


def _merge_rewrite_candidate(
    context: _RewriteSelectionContext,
    group: ManifestRewriteConfig,
    configured_path: str,
    candidate: ConfinedPath,
) -> None:
    """Validate one candidate and merge its dependency and string rules."""
    manifest_path = candidate.path
    if manifest_path.resolve() in context.skipped_manifests:
        _log.debug(
            "Skipping configured manifest rewrite %s because it is a "
            "workspace-owned manifest at %s",
            configured_path,
            manifest_path,
        )
        return
    selection = context.selections.setdefault(manifest_path, _RewriteSelection())
    selection.dependencies |= group.dependencies
    selection.string_values.extend(group.string_values)


def _expand_path(
    workspace_root: Path, configured_path: str
) -> tuple[ConfinedPath, ...]:
    """Expand a glob or return its single literal candidate."""
    if not glob.has_magic(configured_path):
        return (_confine_manifest_path(workspace_root, configured_path),)
    literal_candidate = workspace_root / configured_path
    if _is_existing_literal_candidate(literal_candidate, configured_path):
        return (_confine_manifest_path(workspace_root, configured_path),)
    return _expand_manifest_rewrite_glob(workspace_root, configured_path)


def _is_existing_literal_candidate(candidate: Path, configured_path: str) -> bool:
    """Keep an existing path literal even when its name contains glob syntax."""
    try:
        candidate.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        # Windows reports a filename containing a glob metacharacter as
        # ERROR_INVALID_NAME, which CPython maps to EINVAL. No literal file can
        # carry that character, so the pattern must reach glob expansion.
        if getattr(exc, "winerror", None) == _WINDOWS_INVALID_NAME_WINERROR:
            return False
        message = (
            "Could not inspect literal manifest rewrite path "
            f"{configured_path!r}: {exc}"
        )
        raise ManifestRewriteError(message) from exc
    return True


def _expand_manifest_rewrite_glob(
    workspace_root: Path,
    configured_path: str,
) -> tuple[ConfinedPath, ...]:
    """Expand a configured glob and reject an empty result."""
    try:
        matches = expand_confined_glob(
            workspace_root,
            configured_path,
            key="manifest rewrite",
            options=GlobOptions(
                expect=MatchExpectation.REGULAR_FILE,
                unmatched=UnmatchedPolicy.ERROR,
            ),
        )
    except MissingBumpTargetError as exc:
        message = f"Manifest rewrite glob matched no files: {configured_path}"
        raise ManifestRewriteError(message) from exc
    except BumpPreflightError as exc:
        message = f"Could not expand manifest rewrite glob {configured_path!r}: {exc}"
        raise ManifestRewriteError(message) from exc
    for match in matches:
        _require_cargo_manifest(match, configured_path)
    return matches


def _confine_manifest_path(
    workspace_root: Path,
    configured_path: str,
) -> ConfinedPath:
    """Confine one literal manifest path and require a Cargo manifest name."""
    try:
        candidate = confine_relative_path(
            workspace_root, configured_path, key="manifest rewrite"
        )
    except BumpPreflightError as exc:
        message = (
            "Manifest rewrite path must name a regular Cargo.toml file inside "
            f"the workspace: {configured_path}: {exc}"
        )
        raise ManifestRewriteError(message) from exc
    _require_cargo_manifest(candidate, configured_path)
    return candidate


def _require_cargo_manifest(candidate: ConfinedPath, configured_path: str) -> None:
    """Reject a configured selection that is not named ``Cargo.toml``."""
    if candidate.path.name != "Cargo.toml":
        message = (
            f"Manifest rewrite path must point to a Cargo.toml file: {configured_path}"
        )
        raise ManifestRewriteError(message)


def _read_source(path: Path, selection: _RewriteSelection) -> ManifestRewriteSource:
    """Read one selected manifest and attach its merged rewrite rules."""
    try:
        content = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeError) as exc:
        message = f"Could not read manifest rewrite target {path}: {exc}"
        raise ManifestRewriteError(message) from exc
    return ManifestRewriteSource(
        path=path,
        content=content,
        dependencies=selection.dependencies,
        string_values=tuple(selection.string_values),
    )
