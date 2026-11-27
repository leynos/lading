"""Prepare configured non-member manifest rewrites for the bump plan.

The planner reads and rewrites selected fixture manifests without mutating
them. This adapter confines each planned destination and proposes its complete
new text to the bump plan, so fixture writes commit with the manifest stage and
before documentation, README and lockfile work.
"""

import collections.abc as cabc
import time
import typing as typ

from tomlkit.exceptions import TOMLKitError

from lading.commands import (
    bump_manifest_rewrite_filesystem,
    bump_manifest_rewrite_metrics,
    bump_manifest_rewrites,
    bump_toml,
)
from lading.commands.bump_manifest_rewrite_metrics import (
    ManifestRewriteMetricLabels,
)
from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewriteVersions,
)
from lading.commands.bump_plan import PlanBuilder, WriteCategory
from lading.utils.confinement import (
    BumpPreflightError,
    confine_relative_path,
    establish_workspace_root,
)

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from pathlib import Path

    from tomlkit.toml_document import TOMLDocument

    from lading.commands.bump_manifest_rewrites import ManifestRewritePlan
    from lading.commands.bump_manifests import _BumpContext
    from lading.config import ManifestRewriteConfig
    from lading.workspace import WorkspaceCrate


def prepare_manifest_rewrites(
    context: _BumpContext,
    target_version: str,
    builder: PlanBuilder,
) -> tuple[ManifestRewritePlan, ...]:
    """Plan configured fixture rewrites and propose their new text.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    target_version : str
        Version to apply.
    builder : PlanBuilder
        Proposal sink.

    Returns
    -------
    tuple[ManifestRewritePlan, ...]
        The planned rewrites, empty when none are configured.

    Raises
    ------
    ManifestRewriteError
        If a configured manifest is unsafe, unreadable, or cannot be planned.
    """
    rewrite_groups = context.configuration.bump.manifest_rewrites
    if not rewrite_groups:
        return ()
    started_at = time.perf_counter()
    dry_run = context.base_options.dry_run
    try:
        plans = _plan_rewrites(context, target_version, rewrite_groups)
        _propose_rewrites(context, builder, plans)
    except ManifestRewriteError:
        _record_metrics(
            time.perf_counter() - started_at,
            outcome="failed",
            dry_run=dry_run,
            error_category="planning",
        )
        raise
    _record_metrics(
        time.perf_counter() - started_at,
        outcome="planned",
        dry_run=dry_run,
        error_category="none",
    )
    return plans


def _plan_rewrites(
    context: _BumpContext,
    target_version: str,
    rewrite_groups: cabc.Sequence[ManifestRewriteConfig],
) -> tuple[ManifestRewritePlan, ...]:
    """Read selected sources and plan their rewritten text."""
    sources = bump_manifest_rewrite_filesystem.read_manifest_rewrite_sources(
        context.root_path,
        rewrite_groups,
        tuple(crate.manifest_path for crate in context.workspace.crates),
    )
    return bump_manifest_rewrites.plan_manifest_rewrites(
        sources,
        ManifestRewriteVersions(
            updated_crate_names=context.updated_crate_names,
            pre_bump_versions=_pre_bump_versions_from_manifests(context),
            target_version=target_version,
        ),
    )


def _pre_bump_versions_from_manifests(context: _BumpContext) -> dict[str, str]:
    """Derive member versions from disk and require graph agreement.

    Configured rewrites use these versions as their pre-bump source. Deriving
    them here, rather than trusting the supplied graph, stops a caller that
    reuses a graph after an earlier bump from planning against stale versions
    and silently leaving fixture text at the previous target.

    Returns
    -------
    dict[str, str]
        Each member crate's effective version read from its manifest.
    """
    workspace_document = _parse_manifest(context.workspace_manifest)
    inherited_version = _inherited_workspace_version(workspace_document)
    versions: dict[str, str] = {}
    for crate in context.workspace.crates:
        declared_version = _declared_crate_version(crate, inherited_version)
        _require_graph_version(crate, declared_version)
        versions[crate.name] = declared_version
    return versions


def _parse_manifest(manifest_path: Path) -> TOMLDocument:
    """Read and parse one manifest for version evidence."""
    try:
        return bump_toml.parse_manifest(manifest_path)
    except (OSError, UnicodeDecodeError, TOMLKitError) as exc:
        message = f"Could not read manifest for rewrite planning {manifest_path}: {exc}"
        raise ManifestRewriteError(message, failed_path=manifest_path) from exc


def _inherited_workspace_version(document: TOMLDocument) -> str | None:
    """Return ``[workspace.package].version`` when it is a string."""
    package_table = bump_toml.select_table(document, ("workspace", "package"))
    if package_table is None:
        return None
    version = package_table.get("version")
    return version if isinstance(version, str) else None


def _declared_crate_version(
    crate: WorkspaceCrate,
    inherited_version: str | None,
) -> str:
    """Return the version a member manifest declares for ``crate``."""
    document = _parse_manifest(crate.manifest_path)
    package = document.get("package")
    if not isinstance(package, cabc.Mapping):
        message = f"Member manifest has no [package] table: {crate.manifest_path}"
        raise ManifestRewriteError(message, failed_path=crate.manifest_path)
    _require_package_name(package, crate)
    match package.get("version"):
        case str() as direct_version:
            return direct_version
        case {"workspace": True}:
            if inherited_version is None:
                message = (
                    "Workspace-inherited package version is unavailable in "
                    f"{crate.manifest_path}"
                )
                raise ManifestRewriteError(message, failed_path=crate.manifest_path)
            return inherited_version
        case None:
            # Cargo treats an omitted package version as 0.0.0.
            return "0.0.0"
        case _:
            message = (
                "Member manifest declares a non-string package version: "
                f"{crate.manifest_path}"
            )
            raise ManifestRewriteError(message, failed_path=crate.manifest_path)


def _require_package_name(
    package: cabc.Mapping[str, object],
    crate: WorkspaceCrate,
) -> None:
    """Require the manifest package name to match workspace discovery."""
    if bump_toml.value_as_string(package.get("name")) == crate.name:
        return
    message = (
        f"Manifest package name differs from workspace discovery: {crate.manifest_path}"
    )
    raise ManifestRewriteError(message, failed_path=crate.manifest_path)


def _require_graph_version(crate: WorkspaceCrate, declared_version: str) -> None:
    """Reject a supplied graph whose recorded version is stale."""
    if declared_version == crate.version:
        return
    message = (
        f"Injected workspace graph records crate {crate.name!r} at version "
        f"{crate.version}, but {crate.manifest_path} declares {declared_version}"
    )
    raise ManifestRewriteError(message, failed_path=crate.manifest_path)


def _propose_rewrites(
    context: _BumpContext,
    builder: PlanBuilder,
    plans: cabc.Sequence[ManifestRewritePlan],
) -> None:
    """Confine each planned destination and propose its rewritten text."""
    root = establish_workspace_root(context.root_path)
    for plan in plans:
        relative = _relative_rewrite_path(plan, root)
        key = f"manifest rewrite {relative.as_posix()}"
        try:
            destination = confine_relative_path(root, relative.as_posix(), key=key)
        except BumpPreflightError as exc:
            message = f"{key}: cannot write the planned rewrite: {exc}"
            raise ManifestRewriteError(message) from exc
        builder.propose(destination, plan.new_text, WriteCategory.FIXTURE_MANIFEST)


def _relative_rewrite_path(plan: ManifestRewritePlan, root: Path) -> Path:
    """Return the planned destination relative to the canonical workspace root."""
    try:
        return plan.path.relative_to(root)
    except ValueError as exc:
        message = f"Manifest rewrite plan escaped the workspace root: {plan.path}"
        raise ManifestRewriteError(message) from exc


def _record_metrics(
    duration_seconds: float,
    *,
    outcome: str,
    dry_run: bool,
    error_category: str,
) -> None:
    """Record one planning outcome and its duration."""
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        duration_seconds,
        ManifestRewriteMetricLabels(
            phase="planning",
            outcome=outcome,
            dry_run=dry_run,
            error_category=error_category,
        ),
    )


def record_application_metrics(
    context: _BumpContext,
    duration_seconds: float,
    *,
    failed: bool,
) -> None:
    """Record the commit-phase outcome for configured rewrites.

    Parameters
    ----------
    context : _BumpContext
        The initialised bump context.
    duration_seconds : float
        Time spent committing the prepared plan.
    failed : bool
        Whether the commit failed.
    """
    if not context.configuration.bump.manifest_rewrites:
        return
    dry_run = context.base_options.dry_run
    if failed:
        outcome, error_category = "failed", "write"
    elif dry_run:
        outcome, error_category = "skipped", "none"
    else:
        outcome, error_category = "applied", "none"
    bump_manifest_rewrite_metrics.record_manifest_rewrite_metrics(
        duration_seconds,
        ManifestRewriteMetricLabels(
            phase="application",
            outcome=outcome,
            dry_run=dry_run,
            error_category=error_category,
        ),
    )
