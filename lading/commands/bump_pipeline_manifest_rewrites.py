"""Coordinate configured non-member manifest rewrite stages.

The pipeline plans these rewrites before any writes and applies them after
workspace and member manifests. A failed manifest stage restores changed
manifests so a retry still has the original crate versions.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import typing as typ
from pathlib import Path

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import (
    bump_manifest_rewrite_application,
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrite_metrics,
    bump_manifest_rewrites,
    bump_pipeline_manifest_recovery,
)
from lading.commands.bump_manifest_rewrite_metrics import (
    ManifestRewriteMetricLabels,
)
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteWriteError,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.commands.bump_manifests import _BumpContext

_log = logging.getLogger(__name__)


@dc.dataclass(slots=True)
class _ManifestRewriteStage:
    """Configured rewrite plans, timing, and workspace recovery snapshots."""

    is_configured: bool = False
    plans: tuple[bump_manifest_rewrites.ManifestRewritePlan, ...] = ()
    planning_duration_seconds: float = 0.0
    workspace_manifest_snapshots: tuple[tuple[Path, str], ...] = ()
    pre_bump_versions: dict[str, str] = dc.field(default_factory=dict)
    expected_version_values: dict[Path, tuple[tuple[tuple[str, ...], str], ...]] = (
        dc.field(default_factory=dict)
    )
    expected_manifest_contents: dict[Path, str] = dc.field(default_factory=dict)
    written_manifest_contents: dict[Path, str] = dc.field(default_factory=dict)
    pending_manifest_contents: dict[Path, str] = dc.field(default_factory=dict)
    written_manifest_destinations: dict[Path, Path] = dc.field(default_factory=dict)
    rollback_baselines: dict[Path, str] = dc.field(default_factory=dict)


def _plan_non_member_manifest_rewrites(
    context: _BumpContext,
    target_version: str,
) -> _ManifestRewriteStage:
    """Plan rewrites and snapshot workspace manifests before the first write."""
    rewrite_groups = context.configuration.bump.manifest_rewrites
    if not rewrite_groups:
        return _ManifestRewriteStage()
    clock = context.base_options.clock
    planning_started_at = clock()
    member_manifest_paths = tuple(
        crate.manifest_path for crate in context.workspace.crates
    )
    try:
        workspace_manifest_snapshots = _read_workspace_manifest_snapshots(
            context.workspace_manifest, member_manifest_paths
        )
        pre_bump_versions = _pre_bump_versions_from_snapshots(
            context, workspace_manifest_snapshots
        )
        sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
            context.root_path, rewrite_groups, member_manifest_paths
        )
        plans = bump_manifest_rewrites.plan_manifest_rewrites(
            sources,
            bump_manifest_rewrites.ManifestRewriteVersions(
                updated_crate_names=context.updated_crate_names,
                pre_bump_versions=pre_bump_versions,
                target_version=target_version,
            ),
        )
    except ManifestRewriteError:
        bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
            clock() - planning_started_at,
            ManifestRewriteMetricLabels(
                phase="planning",
                outcome="failed",
                dry_run=context.base_options.dry_run,
                error_category="planning",
            ),
        )
        raise
    planning_duration = clock() - planning_started_at
    bump_manifest_rewrite_metrics.record_manifest_rewrite_planned(
        dry_run=context.base_options.dry_run
    )
    return _ManifestRewriteStage(
        is_configured=True,
        plans=plans,
        planning_duration_seconds=planning_duration,
        workspace_manifest_snapshots=workspace_manifest_snapshots,
        pre_bump_versions=pre_bump_versions,
        expected_version_values=_expected_version_values_from_snapshots(
            workspace_manifest_snapshots
        ),
    )


def _pre_bump_versions_from_snapshots(
    context: _BumpContext,
    snapshots: cabc.Sequence[tuple[Path, str]],
) -> dict[str, str]:
    """Read current package versions from the same source snapshots as recovery."""
    text_by_path = dict(snapshots)
    workspace_document = _parse_manifest_snapshot(
        context.workspace_manifest,
        text_by_path[context.workspace_manifest],
    )
    workspace_table = workspace_document.get("workspace")
    workspace_package = (
        workspace_table.get("package")
        if isinstance(workspace_table, cabc.Mapping)
        else None
    )
    inherited_version = (
        workspace_package.get("version")
        if isinstance(workspace_package, cabc.Mapping)
        else None
    )
    versions: dict[str, str] = {}
    for crate in context.workspace.crates:
        manifest_path = crate.manifest_path
        document = _parse_manifest_snapshot(manifest_path, text_by_path[manifest_path])
        package = document.get("package")
        package_version = (
            package.get("version") if isinstance(package, cabc.Mapping) else None
        )
        match package_version:
            case str() as version:
                versions[crate.name] = str(version)
            case {"workspace": True}:
                if not isinstance(inherited_version, str):
                    message = (
                        "Workspace-inherited package version is unavailable in "
                        f"{context.workspace_manifest}"
                    )
                    raise ManifestRewriteError(
                        message, failed_path=context.workspace_manifest
                    )
                versions[crate.name] = str(inherited_version)
            case _:
                versions[crate.name] = crate.version
    return versions


def _expected_version_values_from_snapshots(
    snapshots: cabc.Sequence[tuple[Path, str]],
) -> dict[Path, tuple[tuple[tuple[str, ...], str], ...]]:
    """Capture direct package version selectors for optimistic validation."""
    values_by_path: dict[Path, tuple[tuple[tuple[str, ...], str], ...]] = {}
    for manifest_path, content in snapshots:
        document = _parse_manifest_snapshot(manifest_path, content)
        expected: list[tuple[tuple[str, ...], str]] = []
        package = document.get("package")
        if isinstance(package, cabc.Mapping) and isinstance(
            package.get("version"), str
        ):
            expected.append((("package",), str(package["version"])))
        workspace = document.get("workspace")
        workspace_package = (
            workspace.get("package") if isinstance(workspace, cabc.Mapping) else None
        )
        if isinstance(workspace_package, cabc.Mapping) and isinstance(
            workspace_package.get("version"), str
        ):
            expected.append((
                ("workspace", "package"),
                str(workspace_package["version"]),
            ))
        values_by_path[manifest_path] = tuple(expected)
    return values_by_path


def validate_pre_bump_versions(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
) -> None:
    """Ensure source versions used by fixture plans remain current before writes."""
    current_snapshots = _read_workspace_manifest_snapshots(
        context.workspace_manifest,
        tuple(crate.manifest_path for crate in context.workspace.crates),
    )
    current_versions = _pre_bump_versions_from_snapshots(context, current_snapshots)
    if current_versions == stage.pre_bump_versions:
        return
    changed_crate = next(
        name
        for name, expected in stage.pre_bump_versions.items()
        if current_versions.get(name) != expected
    )
    manifest_path = next(
        crate.manifest_path
        for crate in context.workspace.crates
        if crate.name == changed_crate
    )
    message = (
        "Workspace crate version changed after manifest rewrite planning: "
        f"{manifest_path}"
    )
    raise ManifestRewriteError(message, failed_path=manifest_path)


def _parse_manifest_snapshot(path: Path, content: str) -> TOMLDocument:
    """Parse a captured manifest and retain its path in failures."""
    try:
        return parse_toml(content)
    except TOMLKitError as exc:
        message = f"Could not parse manifest snapshot {path}: {exc}"
        raise ManifestRewriteError(message, failed_path=path) from exc


def _read_workspace_manifest_snapshots(
    workspace_manifest: Path,
    member_manifest_paths: cabc.Collection[Path],
) -> tuple[tuple[Path, str], ...]:
    """Read original workspace and member text for failed-stage recovery."""
    paths = dict.fromkeys((workspace_manifest, *member_manifest_paths))
    snapshots: list[tuple[Path, str]] = []
    for manifest_path in paths:
        try:
            content = manifest_path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeError) as exc:
            message = (
                "Could not snapshot workspace manifest for rewrite recovery "
                f"{manifest_path}: {exc}"
            )
            raise ManifestRewriteError(message, failed_path=manifest_path) from exc
        snapshots.append((manifest_path, content))
    return tuple(snapshots)


def _apply_non_member_manifest_rewrites(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
) -> None:
    """Apply planned fixture rewrites and restore workspace versions on failure."""
    if not stage.is_configured:
        return
    dry_run = context.base_options.dry_run
    clock = context.base_options.clock
    _log.debug(
        "Non-member manifest rewrites: %d planned manifest(s), dry_run=%s",
        len(stage.plans),
        dry_run,
    )
    application_started_at = clock()
    try:
        rewritten_paths = bump_manifest_rewrite_application.apply_manifest_rewrites(
            stage.plans,
            workspace_root=context.root_path,
            dry_run=dry_run,
            write_callbacks=(
                bump_manifest_rewrite_application._ManifestRewriteWriteCallbacks(
                    before_write=(
                        bump_pipeline_manifest_recovery.manifest_write_recorder(stage)
                    ),
                    after_write=(
                        bump_pipeline_manifest_recovery.manifest_write_completion_recorder(
                            stage
                        )
                    ),
                )
            ),
        )
    except BaseException as rewrite_error:
        if _should_recover_manifest_rewrite_failure(rewrite_error, dry_run=dry_run):
            recovery_error = _recover_failed_manifest_rewrite_application(
                context,
                stage,
                changed_manifests,
                rewrite_error,
            )
            _record_failed_manifest_rewrite_metric(
                stage,
                application_started_at,
                clock,
                dry_run=dry_run,
            )
            if recovery_error is not None:
                raise recovery_error from rewrite_error
        raise
    # The resolver already canonicalized these paths; resolving after a write
    # could follow a replaced symlink and report a different manifest.
    changed_manifests.update(rewritten_paths)
    outcome = "skipped" if dry_run or not stage.plans else "applied"
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        stage.planning_duration_seconds + clock() - application_started_at,
        ManifestRewriteMetricLabels(
            phase="application",
            outcome=outcome,
            dry_run=dry_run,
            error_category="none",
        ),
    )


def _should_recover_manifest_rewrite_failure(
    rewrite_error: BaseException,
    *,
    dry_run: bool,
) -> bool:
    """Select handled write failures while preserving dry-run interrupts."""
    return isinstance(rewrite_error, ManifestRewriteError) or (
        not isinstance(rewrite_error, Exception) and not dry_run
    )


def _recover_failed_manifest_rewrite_application(
    context: _BumpContext,
    stage: _ManifestRewriteStage,
    changed_manifests: set[Path],
    rewrite_error: BaseException,
) -> ManifestRewriteWriteError | None:
    """Restore outputs after a handled manifest rewrite failure."""
    interrupted = not isinstance(rewrite_error, Exception)
    if interrupted:
        bump_pipeline_manifest_recovery.record_completed_rewrites_for_interruption(
            stage, changed_manifests, rewrite_error
        )
    recovery_error = bump_pipeline_manifest_recovery.restore_changed_manifests(
        stage,
        changed_manifests,
        rewrite_error,
        workspace_root=context.root_path,
    )
    if recovery_error is not None and interrupted:
        rewrite_error.add_note(str(recovery_error))
        return None
    return recovery_error


def _record_failed_manifest_rewrite_metric(
    stage: _ManifestRewriteStage,
    application_started_at: float,
    clock: cabc.Callable[[], float],
    *,
    dry_run: bool,
) -> None:
    """Record the failed duration of a handled rewrite application."""
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        stage.planning_duration_seconds + clock() - application_started_at,
        ManifestRewriteMetricLabels(
            phase="application",
            outcome="failed",
            dry_run=dry_run,
            error_category="write",
        ),
    )
