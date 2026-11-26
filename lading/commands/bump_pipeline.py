"""Run the ordered stages of a version bump.

The coordinator initializes shared context, then calls this module to update
the workspace manifest, member manifests, configured non-member manifests,
documentation, readmes, and lockfiles. Non-member rewrites are planned before
the first write and applied between member manifests and later stages.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ

from lading.commands import (
    bump_docs,
    bump_lockfiles,
    bump_manifests,
    bump_pipeline_crate_manifests,
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_rewrites,
    bump_pipeline_manifest_sources,
    bump_readme,
    bump_workspace_write_lock,
)
from lading.commands.bump_manifests import (
    _WORKSPACE_SELECTORS,
    _freeze_dependency_sections,
    _ManifestUpdateContext,
    _workspace_dependency_sections,
)
from lading.commands.bump_output import BumpChanges

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.bump_manifest_rewrite_model import ManifestRewriteWriteError
    from lading.commands.bump_manifests import _BumpContext

_log = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class _BumpAuxiliaryChanges:
    """Changed non-manifest paths produced by the bump pipeline."""

    documents: cabc.Sequence[Path]
    readmes: cabc.Sequence[Path]
    lockfiles: cabc.Sequence[Path]


def _run_pipeline(context: _BumpContext, target_version: str) -> BumpChanges:
    """Run one bump under the workspace lock and return its ordered changes.

    A live run holds the lock from planning until the last stage completes, so
    a concurrent bump cannot plan from a half-applied workspace or accept a
    version that this run then rolls back. Dry runs write nothing and do not
    take the lock.

    Returns
    -------
    BumpChanges
        Ordered changes produced by the completed stages.
    """
    if context.base_options.dry_run:
        return _run_pipeline_stages(context, target_version)
    with bump_workspace_write_lock.workspace_write_lock(context.root_path):
        return _run_pipeline_stages(context, target_version)


def _run_pipeline_stages(context: _BumpContext, target_version: str) -> BumpChanges:
    """Run update stages and return their ordered changes."""
    changed_manifests: set[Path] = set()
    rewrite_stage = bump_pipeline_manifest_rewrites._plan_non_member_manifest_rewrites(
        context, target_version
    )
    _process_workspace_and_crate_manifests(
        context, target_version, rewrite_stage, changed_manifests
    )
    bump_pipeline_manifest_rewrites._apply_non_member_manifest_rewrites(
        context, target_version, rewrite_stage, changed_manifests
    )
    changed_documents = _process_documentation_files(context, target_version)
    changed_readmes = _process_readme_transposition(
        context, dry_run=context.base_options.dry_run
    )
    changed_lockfiles = _process_lockfiles(context, changed_manifests)
    return _prepare_sorted_changes(
        context,
        changed_manifests,
        _BumpAuxiliaryChanges(
            documents=tuple(changed_documents),
            readmes=tuple(changed_readmes),
            lockfiles=changed_lockfiles,
        ),
    )


def _process_workspace_and_crate_manifests(
    context: _BumpContext,
    target_version: str,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage,
    changed_manifests: set[Path],
) -> None:
    """Restore pre-bump manifests if a workspace manifest stage fails."""
    try:
        _update_workspace_and_crate_manifests(
            context, target_version, rewrite_stage, changed_manifests
        )
    except BaseException as stage_error:
        rollback_error = _restore_manifest_stage_after_failure(
            context,
            rewrite_stage,
            changed_manifests,
            stage_error,
        )
        if rollback_error is not None:
            for note in getattr(stage_error, "__notes__", ()):
                rollback_error.add_note(note)
            raise rollback_error from stage_error
        raise


def _update_workspace_and_crate_manifests(
    context: _BumpContext,
    target_version: str,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage,
    changed_manifests: set[Path],
) -> None:
    """Validate, update, and revalidate the workspace and member manifests."""
    if rewrite_stage.is_configured:
        bump_pipeline_manifest_sources._validate_pre_bump_versions(
            context, rewrite_stage
        )
    _process_workspace_manifest(
        context, target_version, rewrite_stage, changed_manifests
    )
    _process_crate_manifests(context, target_version, rewrite_stage, changed_manifests)
    if not rewrite_stage.is_configured:
        return
    bump_pipeline_manifest_sources._validate_post_bump_versions(
        context, rewrite_stage, target_version
    )
    _revalidate_written_member_manifests(rewrite_stage)


def _revalidate_written_member_manifests(
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage,
) -> None:
    """Recheck member manifests this run wrote after their member stage."""
    for manifest_path in tuple(rewrite_stage.written_manifest_contents):
        bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
            manifest_path, rewrite_stage
        )


def _restore_manifest_stage_after_failure(
    context: _BumpContext,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage,
    changed_manifests: set[Path],
    stage_error: BaseException,
) -> ManifestRewriteWriteError | None:
    """Restore completed manifest writes unless this is a dry run."""
    if context.base_options.dry_run:
        return None
    bump_pipeline_manifest_recovery.reconcile_manifest_writes_for_failure(
        rewrite_stage, changed_manifests, stage_error
    )
    rollback_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        rewrite_stage,
        changed_manifests,
        stage_error,
        workspace_root=context.root_path,
    )
    if rollback_error is None:
        return None
    if not isinstance(stage_error, Exception):
        stage_error.add_note(str(rollback_error))
        return None
    return rollback_error


def _process_workspace_manifest(
    context: _BumpContext,
    target_version: str,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage,
    changed_manifests: set[Path],
) -> None:
    """Update the workspace manifest when necessary."""
    dependency_sections = _workspace_dependency_sections(context.updated_crate_names)
    workspace_options = dc.replace(
        context.base_options,
        dependency_sections=_freeze_dependency_sections(dependency_sections),
        include_workspace_sections=True,
    )
    update_context = _ManifestUpdateContext(
        options=workspace_options,
        workspace_root=context.root_path,
        workspace_declarations_only=True,
        manifest_entry=context.root_path / "Cargo.toml",
        expected_destination=context.workspace_manifest,
        expected_version_values=rewrite_stage.expected_version_values.get(
            context.workspace_manifest, ()
        ),
        before_write=bump_pipeline_manifest_recovery.manifest_write_recorder(
            rewrite_stage
        ),
        after_write=bump_pipeline_manifest_recovery.manifest_write_completion_recorder(
            rewrite_stage
        ),
    )
    result = bump_manifests._update_manifest(
        context.workspace_manifest,
        _WORKSPACE_SELECTORS,
        target_version,
        update_context,
    )
    rewrite_stage.expected_manifest_contents[context.workspace_manifest] = (
        result.written_text if result.written_text is not None else result.original_text
    )
    if result.was_changed:
        changed_manifests.add(context.workspace_manifest)


def _process_crate_manifests(
    context: _BumpContext,
    target_version: str,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage,
    changed_manifests: set[Path],
) -> None:
    """Update member crate manifests for the workspace."""
    processed_count = 0
    skipped_count = 0
    updated_count = 0
    for crate in context.workspace.crates:
        processed_count += 1
        match bump_pipeline_crate_manifests._apply_crate_manifest_update(
            crate, target_version, context, rewrite_stage
        ):
            case bump_pipeline_crate_manifests._CrateManifestOutcome.SKIPPED:
                skipped_count += 1
            case bump_pipeline_crate_manifests._CrateManifestOutcome.UPDATED:
                changed_manifests.add(
                    bump_pipeline_crate_manifests._manifest_destination(crate, context)
                )
                updated_count += 1
            case bump_pipeline_crate_manifests._CrateManifestOutcome.UNCHANGED:
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
            _log.exception("README transposition failed for crate %r", crate.name)
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
