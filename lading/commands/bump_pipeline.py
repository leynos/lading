"""Run the ordered stages of a version bump.

The coordinator initializes shared context, then calls this module to update
the workspace manifest, member manifests, documentation, readmes, and
lockfiles. The resulting paths flow through :func:`_prepare_sorted_changes`
before the coordinator renders the user-facing summary.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import enum
import logging
import typing as typ

from lading.commands import bump_docs, bump_lockfiles, bump_readme
from lading.commands.bump_manifests import (
    _WORKSPACE_SELECTORS,
    _dependency_sections_for_crate,
    _determine_package_selectors,
    _freeze_dependency_sections,
    _should_skip_crate_update,
    _update_manifest,
    _workspace_dependency_sections,
)
from lading.commands.bump_output import BumpChanges

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump_manifests import _BumpContext
    from lading.workspace import WorkspaceCrate

_log = logging.getLogger(__name__)


class _CrateManifestOutcome(enum.Enum):
    """Closed outcome of processing a single crate manifest.

    Replaces a two-boolean record so the skipped/updated/unchanged states are
    mutually exclusive and the ``both true`` state is unrepresentable.
    """

    SKIPPED = enum.auto()
    UPDATED = enum.auto()
    UNCHANGED = enum.auto()


@dc.dataclass(frozen=True, slots=True)
class _BumpAuxiliaryChanges:
    """Changed non-manifest paths produced by the bump pipeline."""

    documents: cabc.Sequence[Path]
    readmes: cabc.Sequence[Path]
    lockfiles: cabc.Sequence[Path]


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
        changed_path = bump_readme.transpose_readme_to_crate(
            context.root_path,
            crate,
            dry_run=dry_run,
            _source_text=cached_text,
        )
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
    repository = (
        context.base_options.lockfile_repository
        or bump_lockfiles.CargoLockfileRepository()
    )
    if context.base_options.dry_run:
        return repository.resolve_lockfile_paths(context.root_path, lockfile_manifests)
    return repository.regenerate_lockfiles(context.root_path, lockfile_manifests)


def _prepare_sorted_changes(
    context: _BumpContext,
    changed_manifests: set[Path],
    auxiliary_changes: _BumpAuxiliaryChanges,
) -> BumpChanges:
    """Return ordered :class:`BumpChanges` suitable for result rendering."""
    ordered_manifests = tuple(
        sorted(
            changed_manifests,
            key=lambda path: (path != context.workspace_manifest, str(path)),
        )
    )
    ordered_documents: tuple[Path, ...] = tuple(
        sorted(auxiliary_changes.documents, key=lambda path: str(path))
    )
    ordered_lockfiles: tuple[Path, ...] = tuple(
        sorted(
            auxiliary_changes.lockfiles,
            key=lambda path: (path != context.root_path / "Cargo.lock", str(path)),
        )
    )
    ordered_readmes: tuple[Path, ...] = tuple(
        sorted(auxiliary_changes.readmes, key=lambda path: str(path))
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
    """Apply updates for ``crate`` and return the closed manifest outcome."""
    # These crate sets are derived once in `_initialize_bump_context`
    # (issue #97); per-crate processing must not recompute them.
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
