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

A tree is tracked in ``_ACTIVE_STAGING_ROOTS`` from before the copy starts
until after its removal succeeds, so both long windows are covered: a
termination during the copy, and one during the removal. A removal that fails
leaves the target tracked and logs the failure, rather than discarding it
silently.

Examples
--------
Staging copies an entire workspace, so this is shown rather than executed: a
runnable example here would leave a staged tree behind on every test run.

.. code-block:: python

    with staged_workspace(plan, options=options) as staged:
        staged.staging_root.is_dir()
    staged.removed
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

from lading.commands import staging_lock
from lading.commands.publish_manifest import PublishPreparationError

if typ.TYPE_CHECKING:
    from lading.commands.publish import PublishOptions
    from lading.commands.publish_plan import PublishPlan
    from lading.workspace import WorkspaceCrate

LOGGER = logging.getLogger(__name__)

#: The name every automatically created staging directory begins with.
#: `lading clean` finds leftovers by this prefix, so the two must not drift:
#: a publish that stopped using it would leak trees no clean could find, and a
#: clean that stopped using it would either miss them or reach for something
#: else.
STAGING_PREFIX = "lading-publish-"

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
        created = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))
        # Claimed the moment it exists, because this is the shape `lading
        # clean` sweeps: an unclaimed tree here is indistinguishable from one
        # an older release abandoned, and `clean --remove` would take it.
        if not staging_lock.claim(created):
            # Said plainly rather than left in a lock-level log line, because
            # the consequence is the caller's: this publish is running in a
            # tree a concurrent `lading clean --remove` is entitled to delete.
            # It is still not grounds to stop. The claim is a courtesy, and a
            # filesystem that will not lock would otherwise make publishing
            # impossible rather than merely unprotected.
            LOGGER.warning(
                "Staging tree %s is unclaimed, so a concurrent `lading clean "
                "--remove` could take it while this publish runs; continuing",
                created,
            )
        return created

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
    # An automatically created build directory is ours entirely, so it goes.
    # A caller-supplied one may hold their files, so only the copy goes.
    cleanup_target = (
        build_directory
        if auto_created_build_directory
        else _staging_root_for(plan.workspace_root, build_directory)
    )
    # Tracked before the copy starts, not after it finishes. Copying a
    # workspace takes minutes, and a SIGTERM arriving inside that window is
    # exactly what issue #269 is about; a target registered afterwards would
    # leave the partial tree behind.
    if active_options.cleanup:
        _ACTIVE_STAGING_ROOTS.add(cleanup_target)
    try:
        staging_root = _copy_workspace_tree(
            plan.workspace_root,
            build_directory,
            preserve_symlinks=active_options.preserve_symlinks,
        )
    except BaseException:
        # Staging failed part way through, so no caller will ever be handed a
        # block to leave. Whatever was written goes now.
        if active_options.cleanup:
            _remove_staged_tree_or_report(cleanup_target)
        raise
    LOGGER.info("Staged workspace created at %s", staging_root)
    LOGGER.info("Workspace README staging skipped; handled by lading bump")
    if not active_options.cleanup:
        LOGGER.info(
            "Retaining staged workspace at %s; remove it when you are done",
            cleanup_target,
        )
    return (
        PublishPreparation(staging_root=staging_root),
        cleanup_target,
        active_options.cleanup,
    )


def _staging_root_for(workspace_root: Path, build_directory: Path) -> Path:
    """Return where the workspace copy will go, before it is made.

    Knowing this in advance is what lets the cleanup target be registered
    before the copy begins.

    Returns
    -------
    Path
        The staging root :func:`_copy_workspace_tree` will create.
    """
    return build_directory / workspace_root.resolve(strict=True).name


def _remove_staged_tree(cleanup_target: Path) -> None:
    """Remove a staged tree, untracking it only once it is gone.

    An :class:`OSError` from the removal is left to propagate, and the target
    stays tracked, so a later exit hook or signal handler can try again and a
    failure is visible rather than silently forgotten. Callers that must not
    be interrupted by that use :func:`_remove_staged_tree_or_report`.
    """
    # Released before the removal, not after: on Windows the open lock handle
    # would stop the directory holding it being deleted.
    staging_lock.release(cleanup_target)
    if cleanup_target.exists():
        shutil.rmtree(cleanup_target)
    _ACTIVE_STAGING_ROOTS.discard(cleanup_target)


def _remove_staged_tree_or_report(cleanup_target: Path) -> bool:
    """Remove a staged tree, reporting rather than raising on failure.

    Used where an exception would displace something more important: the
    publish result the caller is waiting on, or the termination the signal
    handler is in the middle of honouring.

    Returns
    -------
    bool
        Whether the tree was removed. A retained target stays tracked.
    """
    try:
        _remove_staged_tree(cleanup_target)
    except OSError:
        LOGGER.exception(
            "Could not remove the staged workspace at %s; it is left behind and "
            "still tracked. Remove it by hand if nothing else does",
            cleanup_target,
        )
        return False
    return True


@dc.dataclass(slots=True)
class StagedWorkspace:
    """The staged copy, and what became of it once the block ended.

    Mutable, and deliberately so: ``removed`` is the one thing a caller
    cannot know from inside the block, because the removal happens as the
    block exits. Anything summarising the tree's fate has to read it
    afterwards or it will describe an intention rather than an outcome.

    Attributes
    ----------
    preparation : PublishPreparation
        Where the staged workspace lives.
    removed : bool | None
        Whether the tree was removed. :data:`None` while the block is open,
        and for a block that was asked to retain the tree, since no removal
        was attempted in either case.
    """

    preparation: PublishPreparation
    removed: bool | None = None

    @property
    def staging_root(self) -> Path:
        """Root of the copied workspace used by publication commands.

        Returns
        -------
        Path
            Root of the copied workspace used by publication commands.
        """
        return self.preparation.staging_root

    @property
    def retained(self) -> bool:
        """Whether the staged tree is still on disk.

        Read after the block ends. A removal that failed retains the tree
        just as surely as one that was never asked for, and a summary that
        told the two apart by intention rather than outcome would name a
        directory that is still there as gone.

        Returns
        -------
        bool
            Whether the tree outlived the block.
        """
        return self.removed is not True


@contextlib.contextmanager
def staged_workspace(
    plan: PublishPlan,
    *,
    options: PublishOptions | None = None,
) -> cabc.Iterator[StagedWorkspace]:
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
    StagedWorkspace
        The staged workspace location, valid until the block exits, and the
        removal outcome once it has.
    """
    # `_stage` has already registered the target, from before it began
    # copying, so there is nothing to add here.
    preparation, cleanup_target, cleanup = _stage(plan, options)
    staged = StagedWorkspace(preparation=preparation)
    try:
        yield staged
    finally:
        if cleanup:
            # Reported rather than raised: an exception here would replace
            # whatever the block was already propagating, including the
            # publish failure the caller needs to see.
            staged.removed = _remove_staged_tree_or_report(cleanup_target)
        else:
            # The tree stays, but the claim does not. It says a publish is
            # using the tree, and this one has finished; holding it would
            # keep a file descriptor open for the life of a long-running
            # caller and make `lading clean --remove` skip a tree nothing is
            # reading.
            staging_lock.release(cleanup_target)


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
    # Registered by `_stage`; this form adds only the exit hook.
    preparation, cleanup_target, cleanup = _stage(plan, options)
    if cleanup:
        atexit.register(_remove_staged_tree_or_report, cleanup_target)
    return preparation


def _handle_termination(signal_number: int, frame: object) -> None:
    """Remove staged trees, then let the default disposition end the process.

    Registered only by the command-line entry point. ``atexit`` hooks and
    ``finally`` blocks do not run when a process is terminated by a signal, so
    without this a SIGTERM leaves the staged copy behind.
    """
    del frame
    try:
        for cleanup_target in tuple(_ACTIVE_STAGING_ROOTS):
            LOGGER.warning(
                "Terminated; removing staged workspace at %s", cleanup_target
            )
            _remove_staged_tree_or_report(cleanup_target)
    finally:
        # In a finally block so a cleanup that goes wrong still ends the
        # process. Swallowing the signal would turn a termination request
        # into a hang, which is worse than the tree it failed to remove.
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


def _format_preparation_summary(
    preparation: PublishPreparation, *, retained: bool
) -> tuple[str, ...]:
    """Return formatted summary lines for staging results.

    ``retained`` says whether the staged tree outlives the publish. It is
    required rather than defaulted because the honest answer changed with
    issue #269: cleanup now defaults to on, so by the time a caller reads
    this the tree has usually gone, and a line reading ``Staged workspace
    at:`` alone names a path that no longer exists.

    Returns
    -------
    tuple of str
        The summary lines, the first naming the staged tree and its fate.
    """
    location = f"Staged workspace at: {preparation.staging_root}"
    if not retained:
        location = f"{location} (removed)"
    return (location, "Workspace READMEs are handled by lading bump.")


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
