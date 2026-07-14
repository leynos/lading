"""Prepare an isolated workspace tree for publication.

The helpers in this module copy a planned workspace into a build directory,
resolve crate paths within that copy, and optionally register staged-tree
cleanup. Call :func:`prepare_workspace` before applying staging-time manifest
changes or invoking cargo.

Examples
--------
>>> preparation = prepare_workspace(plan, workspace, options=options)
>>> preparation.staging_root.is_dir()
True
"""

from __future__ import annotations

import atexit
import dataclasses as dc
import logging
import shutil
import tempfile
import typing as typ
from pathlib import Path

from lading.commands.publish_manifest import PublishPreparationError

if typ.TYPE_CHECKING:
    from lading.commands.publish import PublishOptions
    from lading.commands.publish_plan import PublishPlan
    from lading.workspace import WorkspaceCrate, WorkspaceGraph

LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class PublishPreparation:
    """Details about the staged workspace copy.

    Attributes
    ----------
    staging_root:
        Root of the copied workspace used by publication commands.
    """

    staging_root: Path


def _normalise_build_directory(
    workspace_root: Path, build_directory: Path | None
) -> Path:
    """Return a directory suitable for staging workspace artifacts."""
    if build_directory is None:
        return Path(tempfile.mkdtemp(prefix="lading-publish-"))

    candidate = Path(build_directory).expanduser()
    candidate = candidate.resolve(strict=False)

    workspace_root = workspace_root.resolve(strict=True)
    if candidate.is_relative_to(workspace_root):
        message = "Publish build directory cannot reside within the workspace root"
        raise PublishPreparationError(message)

    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        message = f"Cannot create publish build directory: {candidate}"
        raise PublishPreparationError(message) from exc
    return candidate


def _copy_workspace_tree(
    workspace_root: Path, build_directory: Path, *, preserve_symlinks: bool
) -> Path:
    """Copy ``workspace_root`` into ``build_directory`` and return the clone.

    When ``preserve_symlinks`` is :data:`True`, the cloned tree keeps symbolic
    links instead of dereferencing them. This avoids unexpectedly copying large
    directories outside the workspace while still allowing callers to opt into
    dereferencing if required.
    """
    workspace_root = workspace_root.resolve(strict=True)
    staging_root = build_directory / workspace_root.name
    if staging_root.resolve(strict=False).is_relative_to(workspace_root):
        message = "Publish staging directory cannot be nested inside the workspace root"
        raise PublishPreparationError(message)
    if staging_root.exists():
        shutil.rmtree(staging_root)
    try:
        shutil.copytree(workspace_root, staging_root, symlinks=preserve_symlinks)
    except OSError as exc:
        message = f"Cannot copy workspace into staging directory: {staging_root}"
        raise PublishPreparationError(message) from exc
    return staging_root


def prepare_workspace(
    plan: PublishPlan,
    workspace: WorkspaceGraph,
    *,
    options: PublishOptions | None = None,
) -> PublishPreparation:
    """Stage a workspace copy for publishing.

    Parameters
    ----------
    plan:
        Publication plan containing the workspace root.
    workspace:
        Workspace graph associated with the plan.
    options:
        Staging options. Defaults to :class:`PublishOptions` values.

    Returns
    -------
    PublishPreparation
        The staged workspace location.
    """
    if options is None:
        from lading.commands.publish import PublishOptions

        active_options = PublishOptions()
    else:
        active_options = options
    build_directory = _normalise_build_directory(
        plan.workspace_root, active_options.build_directory
    )
    LOGGER.info(
        "Preparing staged workspace for publication under %s",
        build_directory,
    )
    staging_root = _copy_workspace_tree(
        plan.workspace_root,
        build_directory,
        preserve_symlinks=active_options.preserve_symlinks,
    )
    LOGGER.info("Staged workspace created at %s", staging_root)
    LOGGER.info("Workspace README staging skipped; handled by lading bump")
    preparation = PublishPreparation(staging_root=staging_root)
    if active_options.cleanup:
        cleanup_target = staging_root

        def _cleanup() -> None:
            """Remove the staged workspace tree on process exit."""
            shutil.rmtree(cleanup_target, ignore_errors=True)

        atexit.register(_cleanup)
    return preparation


def _format_preparation_summary(preparation: PublishPreparation) -> tuple[str, ...]:
    """Return formatted summary lines for staging results."""
    lines = [f"Staged workspace at: {preparation.staging_root}"]
    lines.append("Workspace READMEs are handled by lading bump.")
    return tuple(lines)


def _resolve_staged_crate_root(
    crate: WorkspaceCrate,
    plan: PublishPlan,
    staging_root: Path,
) -> Path:
    """Return the staged crate root, ensuring it resides within the workspace."""
    try:
        relative_root = crate.root_path.relative_to(plan.workspace_root)
    except ValueError as exc:  # pragma: no cover - defensive guard
        message = (
            f"Crate {crate.name!r} root {crate.root_path} is outside workspace "
            f"{plan.workspace_root}"
        )
        raise PublishPreparationError(message) from exc

    staged_root = staging_root / relative_root
    if not staged_root.exists():  # pragma: no cover - defensive guard
        message = f"Staged crate root not found for {crate.name!r}: {staged_root}"
        raise PublishPreparationError(message)

    return staged_root
