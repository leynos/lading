"""Version bumping command implementation."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import types
import typing as typ

from lading import config as config_module
from lading.commands import bump_docs, bump_lockfiles, bump_toml
from lading.commands.bump_output import (
    BumpChanges,
    _format_result_message,
)
from lading.runtime import CommandRunner
from lading.utils import normalise_workspace_root

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.config import LadingConfig
    from lading.workspace import WorkspaceCrate, WorkspaceGraph

_WORKSPACE_SELECTORS = (("package",), ("workspace", "package"))

_DEPENDENCY_SECTION_BY_KIND: typ.Final[dict[str | None, str]] = {
    None: "dependencies",
    "normal": "dependencies",
    "dev": "dev-dependencies",
    "build": "build-dependencies",
}

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class BumpOptions:
    """Configuration options for bump operations.

    ``rebuild_lockfiles=None`` inherits ``configuration.bump.rebuild_lockfiles``.
    Pass an explicit boolean to override configuration for one run.
    """

    dry_run: bool = False
    rebuild_lockfiles: bool | None = None
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]] = dc.field(
        default_factory=lambda: types.MappingProxyType({})
    )
    include_workspace_sections: bool = False
    runner: CommandRunner | None = None


@dc.dataclass(frozen=True, slots=True)
class _BumpContext:
    """Initialisation context for bump operations."""

    root_path: Path
    configuration: LadingConfig
    workspace: WorkspaceGraph
    base_options: BumpOptions
    workspace_manifest: Path
    excluded: frozenset[str]
    updated_crate_names: frozenset[str]


def run(
    workspace_root: Path | str,
    target_version: str,
    *,
    options: BumpOptions | None = None,
) -> str:
    """Update workspace and crate manifest versions to ``target_version``."""
    context = _initialize_bump_context(workspace_root, options)
    changed_manifests: set[Path] = set()
    _process_workspace_manifest(context, target_version, changed_manifests)
    _process_crate_manifests(context, target_version, changed_manifests)
    changed_documents = _process_documentation_files(context, target_version)
    changed_lockfiles = _process_lockfiles(context, changed_manifests)
    changes = _prepare_sorted_changes(
        context, changed_manifests, changed_documents, changed_lockfiles
    )
    return _format_result_message(
        changes,
        target_version,
        dry_run=context.base_options.dry_run,
        workspace_root=context.root_path,
    )


def _initialize_bump_context(
    workspace_root: Path | str,
    options: BumpOptions | None,
) -> _BumpContext:
    """Return initialised bump context for ``workspace_root``."""
    resolved_options = BumpOptions() if options is None else options
    root_path = normalise_workspace_root(workspace_root)
    configuration = resolved_options.configuration
    if configuration is None:
        configuration = config_module.current_configuration()

    workspace = resolved_options.workspace
    if workspace is None:
        from lading.workspace import load_workspace

        workspace = load_workspace(root_path)

    rebuild_lockfiles = (
        configuration.bump.rebuild_lockfiles
        if resolved_options.rebuild_lockfiles is None
        else resolved_options.rebuild_lockfiles
    )
    base_options = BumpOptions(
        dry_run=resolved_options.dry_run,
        rebuild_lockfiles=rebuild_lockfiles,
        configuration=configuration,
        workspace=workspace,
        runner=resolved_options.runner,
        dependency_sections=resolved_options.dependency_sections,
        include_workspace_sections=resolved_options.include_workspace_sections,
    )
    excluded = frozenset(configuration.bump.exclude)
    updated_crate_names = frozenset(
        crate.name for crate in workspace.crates if crate.name not in excluded
    )
    workspace_manifest = root_path / "Cargo.toml"
    return _BumpContext(
        root_path=root_path,
        configuration=configuration,
        workspace=workspace,
        base_options=base_options,
        workspace_manifest=workspace_manifest,
        excluded=excluded,
        updated_crate_names=updated_crate_names,
    )


def _process_workspace_manifest(
    context: _BumpContext,
    target_version: str,
    changed_manifests: set[Path],
) -> None:
    """Update the workspace manifest when necessary."""
    dependency_sections = _workspace_dependency_sections(context.updated_crate_names)
    workspace_options = dc.replace(
        context.base_options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
        include_workspace_sections=True,
    )
    if _update_manifest(
        context.workspace_manifest,
        _WORKSPACE_SELECTORS,
        target_version,
        workspace_options,
    ):
        changed_manifests.add(context.workspace_manifest)


def _process_crate_manifests(
    context: _BumpContext,
    target_version: str,
    changed_manifests: set[Path],
) -> None:
    """Update member crate manifests for the workspace."""
    for crate in context.workspace.crates:
        if _update_crate_manifest(crate, target_version, context.base_options):
            changed_manifests.add(crate.manifest_path)


def _process_documentation_files(
    context: _BumpContext,
    target_version: str,
) -> set[Path]:
    """Update configured documentation targets for the workspace."""
    documentation_paths = bump_docs.resolve_documentation_targets(
        context.root_path, context.configuration.bump.documentation
    )
    return bump_docs.update_documentation_files(
        documentation_paths,
        target_version,
        context.updated_crate_names,
        dry_run=context.base_options.dry_run,
    )


def _process_lockfiles(
    context: _BumpContext,
    changed_manifests: set[Path],
) -> tuple[Path, ...]:
    """Regenerate Cargo lockfiles when bump changes manifest content."""
    if context.base_options.rebuild_lockfiles is not True or not changed_manifests:
        return ()
    lockfile_manifests = context.configuration.bump.lockfile_manifests
    if context.base_options.dry_run:
        return bump_lockfiles.resolve_lockfile_paths(
            context.root_path, lockfile_manifests
        )
    return bump_lockfiles.regenerate_lockfiles(
        context.root_path,
        lockfile_manifests,
        runner=context.base_options.runner,
    )


def _prepare_sorted_changes(
    context: _BumpContext,
    changed_manifests: set[Path],
    changed_documents: set[Path],
    changed_lockfiles: cabc.Sequence[Path],
) -> BumpChanges:
    """Return ordered :class:`BumpChanges` suitable for result rendering."""
    ordered_manifests = tuple(
        sorted(
            changed_manifests,
            key=lambda path: (path != context.workspace_manifest, str(path)),
        )
    )
    ordered_documents: tuple[Path, ...] = tuple(
        sorted(changed_documents, key=lambda path: str(path))
    )
    ordered_lockfiles: tuple[Path, ...] = tuple(
        sorted(
            changed_lockfiles,
            key=lambda path: (path != context.root_path / "Cargo.lock", str(path)),
        )
    )
    return BumpChanges(
        manifests=ordered_manifests,
        documents=ordered_documents,
        lockfiles=ordered_lockfiles,
    )


def _update_crate_manifest(
    crate: WorkspaceCrate,
    target_version: str,
    options: BumpOptions,
) -> bool:
    """Apply updates for ``crate`` while respecting exclusion rules."""
    configuration, workspace = _validate_bump_options(options)

    excluded = set(configuration.bump.exclude)
    updated_crate_names = {
        member.name for member in workspace.crates if member.name not in excluded
    }

    selectors = _determine_package_selectors(crate.name, excluded)
    dependency_sections = _dependency_sections_for_crate(crate, updated_crate_names)

    if _should_skip_crate_update(selectors, dependency_sections):
        return False

    crate_options = dc.replace(
        options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
    )
    return _update_manifest(
        crate.manifest_path,
        selectors,
        target_version,
        crate_options,
    )

def _validate_bump_options(options: BumpOptions) -> tuple[LadingConfig, WorkspaceGraph]:
    """Validate and extract required configuration and workspace from options.

    Raises
    ------
        ValueError: If configuration or workspace is None.

    Returns
    -------
        Tuple of (configuration, workspace).

    """
    if options.configuration is None or options.workspace is None:
        message = "BumpOptions must supply configuration and workspace."
        raise ValueError(message)
    return options.configuration, options.workspace


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
