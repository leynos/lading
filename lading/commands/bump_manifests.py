"""Manifest rewriting helpers for ``lading bump``.

Extracted from :mod:`lading.commands.bump` (issue #108). This module owns
per-manifest version and dependency-section rewriting; orchestration and
context derivation stay in ``bump``, which re-exports these helpers for the
historical ``bump._update_manifest``-style access used by tests.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import types
import typing as typ

from lading.commands import bump_toml

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump import BumpOptions
    from lading.config import LadingConfig
    from lading.workspace import WorkspaceCrate, WorkspaceGraph

_WORKSPACE_SELECTORS: typ.Final[tuple[tuple[str, ...], ...]] = (
    ("package",),
    ("workspace", "package"),
)

# Derived from the canonical section vocabulary so the kind mapping cannot
# drift from ``bump_toml.DEPENDENCY_SECTIONS`` (issue #103).
_NORMAL_SECTION, _DEV_SECTION, _BUILD_SECTION = bump_toml.DEPENDENCY_SECTIONS

_DEPENDENCY_SECTION_BY_KIND: typ.Final[dict[str | None, str]] = {
    None: _NORMAL_SECTION,
    "normal": _NORMAL_SECTION,
    "dev": _DEV_SECTION,
    "build": _BUILD_SECTION,
}


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
    """Return package selectors for the crate, respecting exclusion rules.

    Parameters
    ----------
    crate_name
        Name of the crate to check.
    excluded
        Collection of excluded crate names.

    Returns
    -------
    tuple[tuple[str, ...], ...]
        Package selectors, or an empty tuple when the crate is excluded.

    """
    return () if crate_name in excluded else (("package",),)


def _should_skip_crate_update(
    selectors: tuple[tuple[str, ...], ...],
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
) -> bool:
    """Return whether a crate update can be skipped for lack of work.

    Parameters
    ----------
    selectors
        Package selectors identified for the crate.
    dependency_sections
        Dependency sections to update for the crate.

    Returns
    -------
    bool
        ``True`` when both ``selectors`` and ``dependency_sections`` are empty.

    """
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
    options: BumpOptions,
) -> bool:
    """Apply ``target_version`` to each table described by ``selectors``.

    Parameters
    ----------
    manifest_path
        Path to the Cargo.toml manifest file.
    selectors
        Key tuples identifying version tables to update.
    target_version
        The target version to apply.
    options
        Bump options controlling dry-run, dependency sections, and whether to
        include workspace-level dependency sections.

    Returns
    -------
    bool
        ``True`` when any changes were made.

    """
    document = bump_toml.parse_manifest(manifest_path)
    changed = False
    for selector in selectors:
        table = bump_toml.select_table(document, selector)
        changed |= bump_toml.assign_version(table, target_version)
    if options.dependency_sections:
        changed |= bump_toml.update_dependency_sections(
            document,
            options.dependency_sections,
            target_version,
            include_workspace_sections=options.include_workspace_sections,
        )
    if changed and not options.dry_run:
        bump_toml.write_atomic_text(manifest_path, document.as_string())
    return changed


def _workspace_dependency_sections(
    updated_crates: cabc.Collection[str],
) -> dict[str, set[str]]:
    """Return dependency names to update for the workspace manifest."""
    crate_names = {name for name in updated_crates if name}
    if not crate_names:
        return {}
    return {section: set(crate_names) for section in bump_toml.DEPENDENCY_SECTIONS}


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


# Re-export internal functions used by tests to maintain backward compatibility
_parse_manifest = bump_toml.parse_manifest
_select_table = bump_toml.select_table
_assign_version = bump_toml.assign_version
_value_matches = bump_toml.value_matches
_update_dependency_sections = bump_toml.update_dependency_sections
_update_dependency_table = bump_toml.update_dependency_table
