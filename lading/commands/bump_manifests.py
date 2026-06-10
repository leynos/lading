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

    from lading.commands.bump import BumpOptions, _BumpContext
    from lading.workspace import WorkspaceCrate

_WORKSPACE_SELECTORS: typ.Final[tuple[tuple[str, ...], ...]] = (
    ("package",),
    ("workspace", "package"),
)

_DEPENDENCY_SECTION_BY_KIND: typ.Final[dict[str | None, str]] = {
    None: "dependencies",
    "normal": "dependencies",
    "dev": "dev-dependencies",
    "build": "build-dependencies",
}


def _update_crate_manifest(
    crate: WorkspaceCrate,
    target_version: str,
    context: _BumpContext,
) -> bool:
    """Apply updates for ``crate`` while respecting exclusion rules.

    The crate sets are read from ``context`` — they are derived exactly once
    in :func:`_initialize_bump_context` (issue #97); helpers must not
    recompute them per crate.
    """
    selectors = _determine_package_selectors(crate.name, context.excluded)
    dependency_sections = _dependency_sections_for_crate(
        crate, context.updated_crate_names
    )

    if _should_skip_crate_update(selectors, dependency_sections):
        return False

    crate_options = dc.replace(
        context.base_options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
    )
    return _update_manifest(
        crate.manifest_path,
        selectors,
        target_version,
        crate_options,
    )


def _determine_package_selectors(
    crate_name: str,
    excluded: cabc.Collection[str],
) -> tuple[tuple[str, ...], ...]:
    """Return package selectors for the crate, respecting exclusion rules.

    Args:
        crate_name: Name of the crate to check.
        excluded: Collection of excluded crate names.

    Returns
    -------
        Package selectors tuple, or empty tuple if crate is excluded.

    """
    return () if crate_name in excluded else (("package",),)


def _should_skip_crate_update(
    selectors: tuple[tuple[str, ...], ...],
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
) -> bool:
    """Check if a crate update should be skipped due to no work required.

    Returns
    -------
        True if both selectors and dependency_sections are empty.

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

    Args:
        manifest_path: Path to the Cargo.toml manifest file.
        selectors: Tuple of key tuples identifying version tables to update.
        target_version: The target version to apply.
        options: Bump options controlling dry-run, dependency sections, and
            whether to include workspace-level dependency sections.

    Returns
    -------
        True if any changes were made.

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
    return {
        "dependencies": set(crate_names),
        "dev-dependencies": set(crate_names),
        "build-dependencies": set(crate_names),
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
        section = _DEPENDENCY_SECTION_BY_KIND.get(dependency.kind, "dependencies")
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
