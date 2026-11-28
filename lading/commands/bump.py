"""Coordinate the ``lading bump`` workflow.

``run()`` loads the workspace context and delegates manifest, documentation,
README, and lockfile updates to :mod:`lading.commands.bump_pipeline`. Keeping
the entry point here gives the CLI a stable command boundary while the pipeline
module owns the ordered update sequence.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import types
import typing as typ
from pathlib import Path

from lading import config as config_module
from lading.commands import (
    bump_diff,
    bump_lockfiles,
    bump_manifests,
    bump_pipeline,
    bump_workspace_write_lock,
)
from lading.commands.bump_output import _format_result_message
from lading.exceptions import LadingError
from lading.utils.confinement import (
    BumpPreflightError,
    UnsafeBumpTargetError,
    confine_relative_path,
    establish_workspace_root,
)
from lading.workspace.graph_paths import graph_relative_manifest

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.commands.bump_manifests import _BumpContext
    from lading.config import LadingConfig
    from lading.workspace import WorkspaceGraph

LOGGER = logging.getLogger(__name__)


class BumpOptionsError(LadingError):
    """Raised when bump options cannot produce a meaningful run."""


@dc.dataclass(frozen=True, slots=True)
class BumpOptions:
    """Configuration options for bump operations.

    Attributes
    ----------
    dry_run : bool, default False
        Preview manifest, documentation, README, and lockfile changes without
        writing files or running state-changing lockfile rebuild commands.
    rebuild_lockfiles : bool | None, default None
        Controls lockfile regeneration after manifest updates. ``None`` inherits
        ``configuration.bump.rebuild_lockfiles``; ``True`` and ``False``
        override configuration for this run.
    configuration : LadingConfig | None, default None
        Loaded lading configuration. When omitted, :func:`run` loads it from
        the workspace root.
    workspace : WorkspaceGraph | None, default None
        Loaded workspace graph. When omitted, :func:`run` inspects the
        workspace root.
    lockfile_repository : bump_lockfiles.LockfileRepository | None, default None
        Port used for lockfile projection and regeneration. ``None`` selects
        the cargo-backed adapter with the default subprocess runner.
    dependency_sections : Mapping[str, Collection[str]]
        Explicit dependency sections to rewrite by crate name.
    include_workspace_sections : bool, default False
        Whether workspace dependency tables should be rewritten as well.
    diff : bool, default False
        Keyword-only. Append a unified diff of each documentation file that
        was (or would be) rewritten. Requires ``dry_run``; a live run is
        rejected because it has no recorded proposal to show.

    Notes
    -----
    Instances are frozen and slot-based. Options are immutable after creation
    and compact, but callers should not rely on dynamic attributes or mutation.
    """

    dry_run: bool = False
    rebuild_lockfiles: bool | None = None
    configuration: LadingConfig | None = None
    workspace: WorkspaceGraph | None = None
    lockfile_repository: bump_lockfiles.LockfileRepository | None = None
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]] = dc.field(
        default_factory=lambda: types.MappingProxyType({})
    )
    include_workspace_sections: bool = False
    # Keyword-only so inserting the field cannot change the meaning of an
    # existing positional BumpOptions call.
    diff: bool = dc.field(default=False, kw_only=True)


def run(
    workspace_root: Path | str,
    target_version: str,
    *,
    options: BumpOptions | None = None,
) -> str:
    """Update workspace and crate manifest versions to ``target_version``.

    Parameters
    ----------
    workspace_root : Path | str
        Filesystem path to the workspace root containing the top-level
        ``Cargo.toml``.
    target_version : str
        Semantic version string to apply to every updated manifest.
    options : BumpOptions | None, optional
        Run configuration such as the dry-run flag, lockfile-rebuild override,
        and pre-loaded configuration and workspace graph. ``None`` loads
        configuration and the workspace from ``workspace_root``.

    Returns
    -------
    str
        Human-readable summary of the manifests, documents, README files, and
        lockfiles that changed, or would change during a dry run. When
        ``options.diff`` is set, the summary is followed by a blank line,
        ``Documentation diff:``, and a unified diff for each rewritten
        documentation file.

    Notes
    -----
    Errors from the delegated update stages propagate to the caller. In
    particular, ``BumpOptionsError`` rejects ``diff`` without ``dry_run``,
    ``ReadmeTranspositionError`` identifies an unsafe workspace README
    adoption, and ``OSError`` identifies an unreadable or unwritable workspace
    file.

    Examples
    --------
    >>> from lading.commands.bump import BumpOptions, run
    >>> run(  # doctest: +SKIP
    ...     "path/to/workspace", "1.2.0", options=BumpOptions(dry_run=True)
    ... )
    'Dry run; would update version to 1.2.0 in ...'
    """
    resolved_options = BumpOptions() if options is None else options
    _validate_bump_options(resolved_options)
    if resolved_options.dry_run:
        return _run_initialised(workspace_root, resolved_options, target_version)
    root_path = establish_workspace_root(workspace_root)
    with bump_workspace_write_lock.workspace_write_lock(root_path):
        return _run_initialised(root_path, resolved_options, target_version)


def _run_initialised(
    workspace_root: Path | str,
    options: BumpOptions,
    target_version: str,
) -> str:
    """Discover the workspace and run the pipeline for one bump."""
    context = _initialize_bump_context(workspace_root, options)
    LOGGER.debug(
        "Bump context initialised: %d excluded crate(s), %d to update",
        len(context.excluded),
        len(context.updated_crate_names),
    )
    changes = bump_pipeline._run_pipeline(context, target_version)
    summary = _format_result_message(
        changes,
        target_version,
        dry_run=context.base_options.dry_run,
        workspace_root=context.root_path,
    )
    if not context.base_options.diff:
        return summary
    diff_text = bump_diff.format_documentation_diff(
        changes.document_previews, context.root_path
    )
    return f"{summary}\n\nDocumentation diff:\n{diff_text}"


def _initialize_bump_context(
    workspace_root: Path | str,
    options: BumpOptions | None,
) -> _BumpContext:
    """Return initialised bump context for ``workspace_root``."""
    resolved_options = BumpOptions() if options is None else options
    _validate_bump_options(resolved_options)
    root_path = establish_workspace_root(workspace_root)
    configuration = resolved_options.configuration
    if configuration is None:
        configuration = config_module.current_configuration()
    workspace = resolved_options.workspace
    if workspace is None:
        from lading.workspace import load_bump_workspace

        workspace = load_bump_workspace(root_path)
    else:
        _validate_injected_workspace(root_path, workspace)
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
        diff=resolved_options.diff,
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


def _validate_injected_workspace(root_path: Path, workspace: WorkspaceGraph) -> None:
    """Validate an injected graph's root and every crate manifest path.

    A programmatic caller may construct the graph itself. Its manifest paths
    become write targets, so they must satisfy the same confinement contract as
    a graph loaded from Cargo before any manifest is read or written.

    Parameters
    ----------
    root_path : Path
        Canonical workspace root established from the requested path.
    workspace : WorkspaceGraph
        Graph supplied through :class:`BumpOptions`.

    Raises
    ------
    BumpPreflightError
        If the graph root differs from ``root_path`` or a crate manifest escapes
        the workspace, is not named ``Cargo.toml``, or is not a confined regular
        file.
    UnsafeBumpTargetError
        If a crate manifest path is not confined beneath the workspace root.
    """
    graph_root = establish_workspace_root(workspace.workspace_root)
    if graph_root != root_path:
        message = (
            f"Injected workspace graph root {graph_root} does not match the "
            f"requested workspace root {root_path}"
        )
        raise BumpPreflightError(message)
    graph_spelling = workspace.workspace_root
    for crate in workspace.crates:
        key = f"workspace member {crate.name!r} manifest"
        relative = graph_relative_manifest(
            root_path, graph_spelling, crate.manifest_path, key=key
        )
        if relative.name != "Cargo.toml":
            message = f"{key} must be named Cargo.toml: {crate.manifest_path}"
            raise UnsafeBumpTargetError(message)
        confine_relative_path(root_path, relative.as_posix(), key=key)
        crate_root = graph_relative_manifest(
            root_path,
            graph_spelling,
            crate.root_path,
            key=f"workspace member {crate.name!r} root",
        )
        if crate_root != relative.parent:
            message = (
                f"workspace member {crate.name!r} root {crate.root_path} does "
                f"not contain its manifest {crate.manifest_path}"
            )
            raise BumpPreflightError(message)


def _validate_bump_options(options: BumpOptions) -> None:
    """Reject option combinations that cannot produce a meaningful run."""
    if options.diff and not options.dry_run:
        message = "--diff requires --dry-run"
        raise BumpOptionsError(message)
