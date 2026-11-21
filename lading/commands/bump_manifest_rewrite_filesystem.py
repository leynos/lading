"""Resolve and read allowlisted manifests for the rewrite planner.

This adapter owns path expansion, containment checks, member filtering and
UTF-8 reads. It returns source records so rewrite policy can run without
filesystem access.
"""

import collections.abc as cabc
import dataclasses as dc
import glob
import logging
import typing as typ
from itertools import starmap
from pathlib import Path

from lading.commands import manifest_paths
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteSource,
)

if typ.TYPE_CHECKING:
    from lading.config import ManifestRewriteConfig, StringValueRewriteConfig

_log = logging.getLogger(__name__)


@dc.dataclass(slots=True)
class _RewriteSelection:
    """Merged rewrite rules selected for one resolved manifest path."""

    dependencies: bool = False
    string_values: list[StringValueRewriteConfig] = dc.field(default_factory=list)


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

    Raises
    ------
    ManifestRewriteError
        If a selected path is unsafe, missing, not a regular Cargo manifest,
        or cannot be read as UTF-8 text.
    """
    if not rewrite_groups:
        return ()
    root = workspace_root.resolve()
    skipped = {
        (root / "Cargo.toml").resolve(),
        *((root / path).resolve() for path in member_manifest_paths),
    }
    selections: dict[Path, _RewriteSelection] = {}
    for group in rewrite_groups:
        for configured_path in group.paths:
            for candidate in _expand_path(root, configured_path):
                manifest_path = manifest_paths.resolve_manifest_candidate(
                    root,
                    candidate,
                    error_type=ManifestRewriteError,
                    label="Manifest rewrite",
                )
                if manifest_path in skipped:
                    _log.debug(
                        "Skipping configured manifest rewrite %s because it "
                        "resolves to a workspace-owned manifest at %s",
                        configured_path,
                        manifest_path,
                    )
                    continue
                if not manifest_path.is_file():
                    message = (
                        "Manifest rewrite path must point to a regular file: "
                        f"{configured_path}"
                    )
                    raise ManifestRewriteError(message)
                selection = selections.setdefault(manifest_path, _RewriteSelection())
                selection.dependencies |= group.dependencies
                selection.string_values.extend(group.string_values)

    return tuple(starmap(_read_source, selections.items()))


def _expand_path(workspace_root: Path, configured_path: str) -> tuple[Path, ...]:
    """Expand a glob or return its single literal candidate."""
    if not glob.has_magic(configured_path):
        return (workspace_root / configured_path,)
    try:
        matches = manifest_paths.expand_manifest_glob_candidates(
            workspace_root,
            configured_path,
            error_type=ManifestRewriteError,
            label="Manifest rewrite",
        )
    except (OSError, ValueError) as exc:
        message = f"Could not expand manifest rewrite glob {configured_path!r}: {exc}"
        raise ManifestRewriteError(message) from exc
    if not matches:
        message = f"Manifest rewrite glob matched no files: {configured_path}"
        raise ManifestRewriteError(message)
    return matches


def _read_source(path: Path, selection: _RewriteSelection) -> ManifestRewriteSource:
    """Read one selected manifest and attach its merged rewrite rules."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        message = f"Could not read manifest rewrite target {path}: {exc}"
        raise ManifestRewriteError(message) from exc
    return ManifestRewriteSource(
        path=path,
        content=content,
        dependencies=selection.dependencies,
        string_values=tuple(selection.string_values),
    )
