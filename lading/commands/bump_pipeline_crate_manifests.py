"""Update one workspace crate manifest during a bump pipeline."""

import dataclasses as dc
import enum
import logging
import typing as typ
from pathlib import Path

from lading.commands import (
    bump_pipeline_manifest_recovery,
    bump_pipeline_manifest_rewrites,
)
from lading.commands.bump_manifests import (
    _dependency_sections_for_crate,
    _determine_package_selectors,
    _freeze_dependency_sections,
    _ManifestUpdateContext,
    _should_skip_crate_update,
    _update_manifest,
)

if typ.TYPE_CHECKING:
    from lading.commands.bump import BumpOptions
    from lading.commands.bump_manifests import _BumpContext
    from lading.workspace import WorkspaceCrate

_log = logging.getLogger(__name__)


class _CrateManifestOutcome(enum.Enum):
    """Closed outcome of processing one crate manifest."""

    SKIPPED = enum.auto()
    UPDATED = enum.auto()
    UNCHANGED = enum.auto()


def _apply_crate_manifest_update(
    crate: WorkspaceCrate,
    target_version: str,
    context: _BumpContext,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage | None = None,
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
    manifest_path, update_context = _prepare_crate_manifest_update(
        crate, crate_options, context, rewrite_stage
    )
    result = _update_manifest(
        manifest_path,
        selectors,
        target_version,
        update_context,
    )
    if result.was_changed:
        _log.debug(
            "Updated crate manifest for crate %r: manifest=%s",
            crate.name,
            manifest_path,
        )
        if not crate_options.dry_run:
            _log.debug(
                "Wrote crate manifest for crate %r: manifest=%s",
                crate.name,
                manifest_path,
            )
        return _CrateManifestOutcome.UPDATED
    _log.debug(
        "Crate manifest already up to date for crate %r: manifest=%s",
        crate.name,
        manifest_path,
    )
    return _CrateManifestOutcome.UNCHANGED


def _prepare_crate_manifest_update(
    crate: WorkspaceCrate,
    options: BumpOptions,
    context: _BumpContext,
    rewrite_stage: bump_pipeline_manifest_rewrites._ManifestRewriteStage | None,
) -> tuple[Path, _ManifestUpdateContext]:
    """Validate and prepare the member manifest destination and write context."""
    is_workspace_manifest = crate.manifest_path in {
        context.workspace_manifest,
        context.root_path / "Cargo.toml",
    }
    manifest_path = (
        context.workspace_manifest if is_workspace_manifest else crate.manifest_path
    )
    bump_pipeline_manifest_recovery.validate_member_manifest_before_update(
        manifest_path, rewrite_stage
    )
    update_context = _ManifestUpdateContext(
        options=options,
        workspace_root=context.root_path,
        manifest_entry=(context.root_path / "Cargo.toml")
        if is_workspace_manifest
        else None,
        expected_destination=context.workspace_manifest
        if is_workspace_manifest
        else crate.manifest_path,
        expected_text=(
            rewrite_stage.expected_manifest_contents.get(manifest_path)
            if rewrite_stage is not None
            else None
        ),
        expected_version_values=(
            rewrite_stage.expected_version_values.get(manifest_path, ())
            if rewrite_stage is not None and not is_workspace_manifest
            else ()
        ),
        before_write=(
            bump_pipeline_manifest_recovery.manifest_write_recorder(rewrite_stage)
            if rewrite_stage is not None
            else None
        ),
        after_write=(
            bump_pipeline_manifest_recovery.manifest_write_completion_recorder(
                rewrite_stage
            )
            if rewrite_stage is not None
            else None
        ),
    )
    return manifest_path, update_context
