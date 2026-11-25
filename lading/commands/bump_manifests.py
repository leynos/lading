"""Manifest rewriting helpers for ``lading bump``.

Extracted from :mod:`lading.commands.bump` (issue #108). This module owns
per-manifest version and dependency-section rewriting. The ordered update
sequence is orchestrated by :mod:`lading.commands.bump_pipeline`, while
``bump`` derives the shared context passed to those pipeline stages.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import types
import typing as typ

from lading.commands import (
    bump_toml,
    bump_toml_dependencies,
    bump_toml_files,
    manifest_paths,
)
from lading.commands.bump_toml_dependency_updates import (
    update_dependency_sections_with_warnings,
)
from lading.exceptions import LadingError

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump import BumpOptions
    from lading.config import LadingConfig
    from lading.workspace import WorkspaceCrate, WorkspaceGraph

_log = logging.getLogger(__name__)

_WORKSPACE_SELECTORS: typ.Final[tuple[tuple[str, ...], ...]] = (
    ("package",),
    ("workspace", "package"),
)

# Derived from the canonical section vocabulary so the kind mapping cannot
# drift from ``bump_toml_dependencies.DEPENDENCY_SECTIONS`` (issue #103).
_NORMAL_SECTION, _DEV_SECTION, _BUILD_SECTION = (
    bump_toml_dependencies.DEPENDENCY_SECTIONS
)

_DEPENDENCY_SECTION_BY_KIND: typ.Final[dict[str | None, str]] = {
    None: _NORMAL_SECTION,
    "normal": _NORMAL_SECTION,
    "dev": _DEV_SECTION,
    "build": _BUILD_SECTION,
}


@dc.dataclass(frozen=True, slots=True)
class _ManifestUpdateContext:
    """Pair version-update options with the boundary for its file write."""

    options: BumpOptions
    workspace_root: Path | None = None
    workspace_declarations_only: bool = False


@dc.dataclass(frozen=True, slots=True)
class _ManifestUpdateResult:
    """Describe source text, output text, and destination of one update."""

    was_changed: bool
    original_text: str
    written_text: str | None = None
    write_destination: Path | None = None


@dc.dataclass(frozen=True, slots=True)
class _BumpContext:
    """Initialisation context for bump operations.

    Derived once by ``bump._initialize_bump_context`` and consumed by
    :func:`bump_pipeline._apply_crate_manifest_update`; the manifest-mutation
    contract lives with this extracted module rather than reaching back into
    ``bump`` internals.
    """

    root_path: Path
    configuration: LadingConfig
    workspace: WorkspaceGraph
    base_options: BumpOptions
    workspace_manifest: Path
    excluded: frozenset[str]
    updated_crate_names: frozenset[str]


def _determine_package_selectors(
    crate_name: str,
    excluded: cabc.Collection[str],
) -> tuple[tuple[str, ...], ...]:
    """Return package selectors for the crate, respecting exclusion rules."""
    return () if crate_name in excluded else (("package",),)


def _should_skip_crate_update(
    selectors: tuple[tuple[str, ...], ...],
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
) -> bool:
    """Return whether a crate update can be skipped for lack of work."""
    return not selectors and not dependency_sections


def _freeze_dependency_sections(
    sections: cabc.Mapping[str, cabc.Collection[str]],
) -> cabc.Mapping[str, cabc.Collection[str]]:
    """Return an immutable mapping for dependency sections."""
    if not sections:
        return types.MappingProxyType({})
    frozen_sections = {key: tuple(sorted(names)) for key, names in sections.items()}
    return types.MappingProxyType(frozen_sections)


def _update_manifest(
    manifest_path: Path,
    selectors: tuple[tuple[str, ...], ...],
    target_version: str,
    update_context: _ManifestUpdateContext,
) -> _ManifestUpdateResult:
    """Apply ``target_version`` to each selected table and dependency section."""
    options = update_context.options
    write_destination = _resolve_manifest_update_destination(
        manifest_path, update_context
    )
    original_text, document = bump_toml_files._read_and_parse_manifest(
        write_destination
    )
    changed = False
    for selector in selectors:
        table = bump_toml.select_table(document, selector)
        changed |= bump_toml.assign_version(table, target_version)
    if options.dependency_sections:
        changed |= update_dependency_sections_with_warnings(
            document,
            options.dependency_sections,
            target_version,
            section_scope=(
                "workspace"
                if update_context.workspace_declarations_only
                else "both"
                if options.include_workspace_sections
                else "direct"
            ),
        )
    if changed and not options.dry_run:
        updated_text = document.as_string()
        current_destination = _resolve_manifest_update_destination(
            manifest_path, update_context
        )
        if current_destination != write_destination:
            message = (
                "Workspace manifest destination changed after it was read: "
                f"{manifest_path}"
            )
            raise LadingError(message)
        bump_toml_files.write_atomic_text(
            write_destination,
            updated_text,
            workspace_root=update_context.workspace_root,
        )
        return _ManifestUpdateResult(
            was_changed=True,
            original_text=original_text,
            written_text=updated_text,
            write_destination=write_destination,
        )
    return _ManifestUpdateResult(was_changed=changed, original_text=original_text)


def _resolve_manifest_update_destination(
    manifest_path: Path,
    update_context: _ManifestUpdateContext,
) -> Path:
    """Resolve one update path under its optional workspace boundary."""
    workspace_root = update_context.workspace_root
    if workspace_root is None:
        return manifest_path.resolve(strict=True)
    return manifest_paths.resolve_workspace_path(
        workspace_root,
        manifest_path,
        error_type=LadingError,
        label="Workspace manifest",
    )


def _workspace_dependency_sections(
    updated_crates: cabc.Collection[str],
) -> dict[str, set[str]]:
    """Return dependency names to update for the workspace manifest."""
    crate_names = {name for name in updated_crates if name}
    if not crate_names:
        return {}
    return {
        section: set(crate_names)
        for section in bump_toml_dependencies.DEPENDENCY_SECTIONS
    }


def _dependency_sections_for_crate(
    crate: WorkspaceCrate,
    updated_crates: cabc.Collection[str],
) -> dict[str, set[str]]:
    """Return dependency names grouped by section for ``crate``."""
    if not crate.dependencies:
        return {}
    targets = {name for name in updated_crates if name}
    if not targets:
        return {}
    sections: dict[str, set[str]] = {}
    for dependency in crate.dependencies:
        if dependency.name not in targets:
            continue
        section = _DEPENDENCY_SECTION_BY_KIND.get(dependency.kind, _NORMAL_SECTION)
        # ``manifest_name`` preserves the dependency key used in the manifest.
        # When a crate is aliased (e.g. ``alpha-core = { package = "alpha" }``)
        # the workspace dependency name remains ``alpha`` while the manifest
        # entry becomes ``alpha-core``. Recording the manifest key ensures the
        # corresponding table entry can be located and updated.
        sections.setdefault(section, set()).add(dependency.manifest_name)
    return sections
