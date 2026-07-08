"""Version bumping command implementation.

This module is the coordinator for the ``bump`` command. :func:`run` is the
entry point invoked by the CLI command layer: given a workspace root and a
target version, it orchestrates every manifest, documentation, README, and
lockfile update and returns a formatted, human-readable summary of the run.

:class:`BumpOptions` is the input configuration (dry-run flag, lockfile-rebuild
override, and the resolved configuration and workspace graph), and
:class:`BumpChanges` is the aggregated result record collecting the files a run
altered; :func:`run` builds a ``BumpChanges`` internally and renders it into the
returned summary message.

Crate-set derivation is a single source of truth. The ``excluded`` and
``updated_crate_names`` sets are computed exactly once in
:func:`_initialize_bump_context` and threaded through
:func:`_apply_crate_manifest_update` via the :class:`_BumpContext`, rather than
being recomputed per crate (issue #97). See ``docs/developers-guide.md`` for the
rule; do not re-derive these sets in per-crate helpers.

This module coordinates rather than implementing file-format specifics, which it
delegates to sibling modules:

- :mod:`lading.commands.bump_docs` — documentation version rewrites.
- :mod:`lading.commands.bump_lockfiles` — lockfile regeneration.
- :mod:`lading.commands.bump_readme` — workspace README transposition.
- :mod:`lading.commands.bump_toml` — low-level TOML manipulation.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import enum
import logging
import types
import typing as typ

from lading import config as config_module
from lading.commands import (
    bump_docs,
    bump_lockfiles,
    bump_manifests,
    bump_readme,
    bump_toml,
)
from lading.commands.bump_manifests import (
    _WORKSPACE_SELECTORS,
    _dependency_sections_for_crate,
    _determine_package_selectors,
    _freeze_dependency_sections,
    _should_skip_crate_update,
    _update_manifest,
    _workspace_dependency_sections,
)
from lading.commands.bump_output import BumpChanges, _format_result_message
from lading.utils import normalise_workspace_root

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump_manifests import _BumpContext
    from lading.config import LadingConfig
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceCrate, WorkspaceGraph

LOGGER = logging.getLogger(__name__)
_log = LOGGER


@dc.dataclass(frozen=True, slots=True)
class BumpOptions:
    """Configuration options for bump operations.

    Attributes
    ----------
    dry_run : bool, default False
        Preview manifest, documentation, and lockfile changes without writing
        files or running state-changing lockfile rebuild commands. Dry-runs
        report the lockfiles that would be rebuilt.
    rebuild_lockfiles : bool | None, default None
        Controls lockfile regeneration after manifest updates. ``None`` inherits
        ``configuration.bump.rebuild_lockfiles``; ``True`` and ``False``
        override configuration for this run.
    configuration : LadingConfig | None, default None
        Loaded lading configuration. Programmatic callers may omit it only when
        they want ``run`` to load configuration from the workspace root.
    workspace : WorkspaceGraph | None, default None
        Loaded workspace graph. Programmatic callers may omit it only when they
        want ``run`` to inspect the workspace.
    command_runner : CommandRunner | None, default None
        Callable with the runtime command-runner interface. It is used for
        lockfile rebuild commands; ``None`` uses the default subprocess runner.
    dependency_sections : Mapping[str, Collection[str]]
        Explicit dependency sections to rewrite by crate name.
    include_workspace_sections : bool, default False
        Whether workspace dependency tables should be rewritten as well.

    Notes
    -----
    Instances are frozen and slot-based. Options are immutable after creation
    and compact, but callers should not rely on dynamic attributes or mutation.
    """

    dry_run: bool = False
    rebuild_lockfiles: bool | None = None
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    command_runner: CommandRunner | None = None
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]] = dc.field(
        default_factory=lambda: types.MappingProxyType({})
    )
    include_workspace_sections: bool = False


class _CrateManifestOutcome(enum.Enum):
    """Closed outcome of processing a single crate manifest.

    Replaces a two-boolean record so the skipped/updated/unchanged states are
    mutually exclusive and the ``both true`` state is unrepresentable.
    """

    SKIPPED = enum.auto()
    UPDATED = enum.auto()
    UNCHANGED = enum.auto()


def run(
    workspace_root: Path | str,
    target_version: str,
    *,
    options: BumpOptions | None = None,
) -> str:
    """Update workspace and crate manifest versions to ``target_version``."""
    context = _initialize_bump_context(workspace_root, options)
    _log.debug(
        "Bump context initialised: %d excluded crate(s), %d to update",
        len(context.excluded),
        len(context.updated_crate_names),
    )
    changed_manifests: set[Path] = set()
    _process_workspace_manifest(context, target_version, changed_manifests)
    _process_crate_manifests(context, target_version, changed_manifests)
    changed_documents = _process_documentation_files(context, target_version)
    changed_readmes = _process_readme_transposition(
        context, dry_run=context.base_options.dry_run
    )
    changed_lockfiles = _process_lockfiles(context, changed_manifests)
    changes = _prepare_sorted_changes(
        context,
        changed_manifests,
        (changed_documents, changed_readmes, changed_lockfiles),
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
    _log.debug(
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
        command_runner=resolved_options.command_runner,
        dependency_sections=resolved_options.dependency_sections,
        include_workspace_sections=resolved_options.include_workspace_sections,
    )
    excluded = frozenset(configuration.bump.exclude)
    updated_crate_names = frozenset(
        crate.name for crate in workspace.crates if crate.name not in excluded
    )
    workspace_manifest = root_path / "Cargo.toml"
    return bump_manifests._BumpContext(
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
    processed_count = 0
    skipped_count = 0
    updated_count = 0
    for crate in context.workspace.crates:
        processed_count += 1
        match _apply_crate_manifest_update(crate, target_version, context):
            case _CrateManifestOutcome.SKIPPED:
                skipped_count += 1
            case _CrateManifestOutcome.UPDATED:
                changed_manifests.add(crate.manifest_path)
                updated_count += 1
            case _CrateManifestOutcome.UNCHANGED:
                pass
    _log.debug(
        "Crate manifest processing complete: %d processed, %d skipped, %d updated",
        processed_count,
        skipped_count,
        updated_count,
    )


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


def _process_readme_transposition(context: _BumpContext, *, dry_run: bool) -> set[Path]:
    """Transpose workspace README files into opted-in member crates."""
    _log.debug("Starting workspace README transposition")
    changed_readmes: set[Path] = set()
    transposed_entry_count = 0
    source_readme_path = context.root_path / "README.md"
    cached_text: str | None = (
        source_readme_path.read_text(encoding="utf-8")
        if source_readme_path.exists()
        else None
    )
    for crate in context.workspace.crates:
        if not crate.readme_is_workspace:
            continue
        transposed_entry_count += 1
        try:
            changed_path = bump_readme.transpose_readme_to_crate(
                context.root_path,
                crate,
                dry_run=dry_run,
                _source_text=cached_text,
            )
        except bump_readme.ReadmeTranspositionError:
            _log.error("README transposition failed for crate %r", crate.name)
            raise
        if changed_path is not None:
            changed_readmes.add(changed_path)
    _log.debug(
        "README transposition complete: %d entries, %d file(s) changed",
        transposed_entry_count,
        len(changed_readmes),
    )
    return changed_readmes


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
        runner=context.base_options.command_runner,
    )


def _prepare_sorted_changes(
    context: _BumpContext,
    changed_manifests: set[Path],
    changed_aux: tuple[set[Path], set[Path], cabc.Sequence[Path]],
) -> BumpChanges:
    """Return ordered :class:`BumpChanges` suitable for result rendering."""
    changed_documents, changed_readmes, changed_lockfiles = changed_aux
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
    ordered_readmes: tuple[Path, ...] = tuple(
        sorted(changed_readmes, key=lambda path: str(path))
    )
    return BumpChanges(
        manifests=ordered_manifests,
        documents=ordered_documents,
        transposed_readmes=ordered_readmes,
        lockfiles=ordered_lockfiles,
    )


def _apply_crate_manifest_update(
    crate: WorkspaceCrate,
    target_version: str,
    context: _BumpContext,
) -> _CrateManifestOutcome:
    """Apply updates for ``crate`` and return the closed manifest outcome.

    The crate sets are read from ``context`` — they are derived exactly once
    in :func:`_initialize_bump_context` (issue #97); helpers must not
    recompute them per crate.
    """
    selectors = _determine_package_selectors(crate.name, context.excluded)
    dependency_sections = _dependency_sections_for_crate(
        crate, context.updated_crate_names
    )

    if _should_skip_crate_update(selectors, dependency_sections):
        _log.debug("Skipping crate manifest update for excluded crate %r", crate.name)
        return _CrateManifestOutcome.SKIPPED

    crate_options = dc.replace(
        context.base_options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
    )
    was_updated = _update_manifest(
        crate.manifest_path,
        selectors,
        target_version,
        crate_options,
    )
    if was_updated:
        _log.debug(
            "Updated crate manifest for crate %r: manifest=%s",
            crate.name,
            crate.manifest_path,
        )
        if not crate_options.dry_run:
            _log.debug(
                "Wrote crate manifest for crate %r: manifest=%s",
                crate.name,
                crate.manifest_path,
            )
    else:
        _log.debug(
            "Crate manifest already up to date for crate %r: manifest=%s",
            crate.name,
            crate.manifest_path,
        )
    return (
        _CrateManifestOutcome.UPDATED
        if was_updated
        else _CrateManifestOutcome.UNCHANGED
    )


# Re-export low-level TOML helpers used by tests for backward compatibility.
_parse_manifest = bump_toml.parse_manifest
_select_table = bump_toml.select_table
_assign_version = bump_toml.assign_version
_value_matches = bump_toml.value_matches
_update_dependency_sections = bump_toml.update_dependency_sections
_update_dependency_table = bump_toml.update_dependency_table
