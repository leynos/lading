"""Prepare an isolated workspace tree for publication.

The helpers in this module copy a planned workspace into a build directory,
resolve crate paths within that copy, and remove the copy afterwards. Enter
:func:`staged_workspace` before applying staging-time manifest changes or
invoking cargo; the staged tree lives exactly as long as that block.

A staged copy is the whole workspace plus its verify build, so its removal is
not best-effort: the context manager removes it on success, on an exception
and on ``KeyboardInterrupt``, and :func:`install_termination_cleanup` removes
it on ``SIGTERM``, which no ``atexit`` hook or ``finally`` block would reach
(issue #269).

Examples
--------
Staging copies an entire workspace, so this is shown rather than executed: a
runnable example here would leave a staged tree behind on every test run.

.. code-block:: python

    with staged_workspace(plan, options=options) as preparation:
        preparation.staging_root.is_dir()
"""

from __future__ import annotations

import atexit
import collections.abc as cabc
import contextlib
import dataclasses as dc
import logging
import shutil
import signal
import tempfile
import typing as typ
from pathlib import Path

from lading.commands.publish_manifest import PublishPreparationError

if typ.TYPE_CHECKING:
    from lading.commands.publish import PublishOptions
    from lading.commands.publish_plan import PublishPlan
    from lading.workspace import WorkspaceCrate

LOGGER = logging.getLogger(__name__)

#: Staged trees the process is responsible for removing. A signal handler
#: installed by the command-line entry point reads this, because a terminated
#: process never reaches an ``atexit`` hook or a ``finally`` block.
_ACTIVE_STAGING_ROOTS: set[Path] = set()


@dc.dataclass(frozen=True, slots=True)
class PublishPreparation:
    """Details about the staged workspace copy.

    Attributes
    ----------
    staging_root : Path
        Root of the copied workspace used by publication commands.
    """

    staging_root: Path


def _normalize_build_directory(
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

    Returns
    -------
    Path
        Root directory of the copied staged workspace.

    Raises
    ------
    PublishPreparationError
        If the staging directory is unsafe or the workspace cannot be copied.
    """
    workspace_root = workspace_root.resolve(strict=True)
    staging_root = build_directory / workspace_root.name
    if staging_root.resolve(strict=False).is_relative_to(workspace_root):
        message = "Publish staging directory cannot be nested inside the workspace root"
        raise PublishPreparationError(message)
    try:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        shutil.copytree(workspace_root, staging_root, symlinks=preserve_symlinks)
    except OSError as exc:
        message = f"Cannot copy workspace into staging directory: {staging_root}"
        raise PublishPreparationError(message) from exc
    return staging_root


def _stage(
    plan: PublishPlan, options: PublishOptions | None
) -> tuple[PublishPreparation, Path, bool]:
    """Copy the workspace and report what, if anything, must be removed later.

    Returns
    -------
    tuple[PublishPreparation, Path, bool]
        The staged workspace, the path whose removal cleans it up, and whether
        cleanup was requested.
    """
    if options is None:
        from lading.commands.publish import PublishOptions

        active_options = PublishOptions()
    else:
        active_options = options
    auto_created_build_directory = active_options.build_directory is None
    build_directory = _normalize_build_directory(
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
    # An automatically created build directory is ours entirely, so it goes.
    # A caller-supplied one may hold their files, so only the copy goes.
    cleanup_target = build_directory if auto_created_build_directory else staging_root
    if not active_options.cleanup:
        LOGGER.info(
            "Retaining staged workspace at %s; remove it when you are done",
            cleanup_target,
        )
    return (
        PublishPreparation(staging_root=staging_root),
        cleanup_target,
        (active_options.cleanup),
    )


def _remove_staged_tree(cleanup_target: Path) -> None:
    """Remove a staged tree and stop tracking it."""
    _ACTIVE_STAGING_ROOTS.discard(cleanup_target)
    shutil.rmtree(cleanup_target, ignore_errors=True)


@contextlib.contextmanager
def staged_workspace(
    plan: PublishPlan,
    *,
    options: PublishOptions | None = None,
) -> cabc.Iterator[PublishPreparation]:
    """Stage a workspace copy and remove it when the block ends.

    This is the form callers should prefer. The removal runs on success, on an
    exception, and on ``KeyboardInterrupt``, so an interrupted publish does not
    leave tens of gigabytes behind (issue #269).

    Parameters
    ----------
    plan:
        Publication plan containing the workspace root.
    options:
        Staging options. Defaults to :class:`PublishOptions` values.

    Yields
    ------
    PublishPreparation
        The staged workspace location, valid until the block exits.
    """
    preparation, cleanup_target, cleanup = _stage(plan, options)
    if cleanup:
        _ACTIVE_STAGING_ROOTS.add(cleanup_target)
    try:
        yield preparation
    finally:
        if cleanup:
            _remove_staged_tree(cleanup_target)


def prepare_workspace(
    plan: PublishPlan,
    *,
    options: PublishOptions | None = None,
) -> PublishPreparation:
    """Stage a workspace copy for publishing, removing it at process exit.

    Prefer :func:`staged_workspace`, whose removal is bounded by a block rather
    than by the lifetime of the process. This form remains for callers that
    cannot express that scope; its cleanup runs from ``atexit``, which a
    terminated process never reaches.

    Parameters
    ----------
    plan:
        Publication plan containing the workspace root.
    options:
        Staging options. Defaults to :class:`PublishOptions` values.

    Returns
    -------
    PublishPreparation
        The staged workspace location.
    """
    preparation, cleanup_target, cleanup = _stage(plan, options)
    if cleanup:
        _ACTIVE_STAGING_ROOTS.add(cleanup_target)
        atexit.register(_remove_staged_tree, cleanup_target)
    return preparation


def _handle_termination(signal_number: int, frame: object) -> None:
    """Remove staged trees, then let the default disposition end the process.

    Registered only by the command-line entry point. ``atexit`` hooks and
    ``finally`` blocks do not run when a process is terminated by a signal, so
    without this a SIGTERM leaves the staged copy behind.
    """
    del frame
    for cleanup_target in tuple(_ACTIVE_STAGING_ROOTS):
        LOGGER.warning("Terminated; removing staged workspace at %s", cleanup_target)
        _remove_staged_tree(cleanup_target)
    signal.signal(signal_number, signal.SIG_DFL)
    signal.raise_signal(signal_number)


def install_termination_cleanup() -> None:
    """Remove staged trees when the process is terminated.

    Called from application bootstrap rather than on import, so the exit-time
    behaviour is a visible lifecycle decision. This follows the precedent set
    for the metrics summary in ADR-004.
    """
    with contextlib.suppress(ValueError):
        # ValueError: not the main thread, where signal handlers cannot be set.
        signal.signal(signal.SIGTERM, _handle_termination)


def _format_preparation_summary(preparation: PublishPreparation) -> tuple[str, ...]:
    """Return formatted summary lines for staging results."""
    return (
        f"Staged workspace at: {preparation.staging_root}",
        "Workspace READMEs are handled by lading bump.",
    )


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
    if not staged_root.exists():
        message = f"Staged crate root not found for {crate.name!r}: {staged_root}"
        raise PublishPreparationError(message)

    return staged_root
