"""Coordinate the ``lading bump`` workflow."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import types
import typing as typ

from lading import config as config_module
from lading.commands import bump_lockfiles, bump_manifests, bump_pipeline
from lading.commands.bump_output import _format_result_message
from lading.utils import normalise_workspace_root

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump_manifests import _BumpContext
    from lading.config import LadingConfig
    from lading.workspace import WorkspaceGraph

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class BumpOptions:
    """Configuration options for bump operations."""

    dry_run: bool = False
    rebuild_lockfiles: bool | None = None
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    lockfile_repository: bump_lockfiles.LockfileRepository | None = None
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]] = dc.field(
        default_factory=lambda: types.MappingProxyType({})
    )
    include_workspace_sections: bool = False


def run(
    workspace_root: Path | str,
    target_version: str,
    *,
    options: BumpOptions | None = None,
) -> str:
    """Update workspace and crate manifest versions to ``target_version``."""
    context = _initialize_bump_context(workspace_root, options)
    LOGGER.debug(
        "Bump context initialised: %d excluded crate(s), %d to update",
        len(context.excluded),
        len(context.updated_crate_names),
    )
    changed_manifests: set[Path] = set()
    bump_pipeline._process_workspace_manifest(
        context, target_version, changed_manifests
    )
    bump_pipeline._process_crate_manifests(context, target_version, changed_manifests)
    changed_documents = bump_pipeline._process_documentation_files(
        context, target_version
    )
    changed_readmes = bump_pipeline._process_readme_transposition(
        context, dry_run=context.base_options.dry_run
    )
    changed_lockfiles = bump_pipeline._process_lockfiles(context, changed_manifests)
    changes = bump_pipeline._prepare_sorted_changes(
        context,
        changed_manifests,
        bump_pipeline._BumpAuxiliaryChanges(
            documents=tuple(changed_documents),
            readmes=tuple(changed_readmes),
            lockfiles=changed_lockfiles,
        ),
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
    LOGGER.debug(
        "rebuild_lockfiles resolution: raw_flag=%r, configured_default=%r, resolved=%r",
        resolved_options.rebuild_lockfiles,
        configuration.bump.rebuild_lockfiles,
        rebuild_lockfiles,
    )
    base_options = BumpOptions(
        dry_run=resolved_options.dry_run,
        rebuild_lockfiles=rebuild_lockfiles,
        configuration=configuration,
        workspace=workspace,
        lockfile_repository=resolved_options.lockfile_repository,
        dependency_sections=resolved_options.dependency_sections,
        include_workspace_sections=resolved_options.include_workspace_sections,
    )
    excluded = frozenset(configuration.bump.exclude)
    updated_crate_names = frozenset(
        crate.name for crate in workspace.crates if crate.name not in excluded
    )
    return bump_manifests._BumpContext(
        root_path=root_path,
        configuration=configuration,
        workspace=workspace,
        base_options=base_options,
        workspace_manifest=root_path / "Cargo.toml",
        excluded=excluded,
        updated_crate_names=updated_crate_names,
    )
