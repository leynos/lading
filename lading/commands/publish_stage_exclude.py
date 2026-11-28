"""Staging adapter for workspace-relative publish exclusions.

Staging copies the workspace before packaging, so entries that packaging never
reads only cost time: build output, Git metadata, virtual environments and tool
caches. This module gathers the facts the exclusion policy needs: it builds the
:func:`shutil.copytree` callback that prunes an excluded directory instead of
walking it, walks the symbolic links a required manifest depends on, and
translates filesystem failures into :class:`StageExcludeInspectionError`.

The spelling rules and the decision about which paths a pattern removes live in
:mod:`lading.stage_exclude_policy`. Those functions take workspace-relative
:class:`~pathlib.PurePosixPath` values and never read the filesystem, so this
module is the only place that resolves, stats or reads a path.
"""

import collections.abc as cabc
import errno
import stat
import typing as typ
from pathlib import Path, PurePosixPath

from lading.commands.publish_manifest import PublishPreparationError
from lading.stage_exclude_policy import (
    dangling_dependency_message,
    excluded_manifest_message,
    match_stage_exclude,
    workspace_relative_prefixes,
)

if typ.TYPE_CHECKING:
    from lading.commands.publish_plan import PublishPlan


class StageExcludeInspectionError(PublishPreparationError):
    """The workspace could not be inspected to check an exclusion.

    Raised when reading the workspace to decide whether a pattern removes a
    required manifest fails, for example because a link cannot be read. It is a
    :class:`~lading.commands.publish_manifest.PublishPreparationError` so
    callers handle it as a staging failure, but it is counted apart from a
    refused pattern: the configuration may be sound and the filesystem not.
    """


def _stage_exclude_ignore(
    workspace_root: Path, patterns: cabc.Sequence[str]
) -> cabc.Callable[[str, list[str]], set[str]]:
    """Build a :func:`shutil.copytree` ``ignore`` callback for ``patterns``.

    The callback returns the entries of each visited directory that match an
    exclusion. Returning a directory's name prunes it before ``copytree``
    enters it, so an excluded build tree is never walked.

    Paths are matched lexically against the root handed to ``copytree``,
    because its recursive ``src`` arguments are that root joined with each
    entry name. Resolving either side could only misalign them.

    Returns
    -------
    cabc.Callable[[str, list[str]], set[str]]
        An ``ignore`` callback suitable for :func:`shutil.copytree`.
    """

    def ignore(directory: str, names: list[str]) -> set[str]:
        """Return the entries of ``directory`` that ``patterns`` exclude."""
        relative_directory = Path(directory).relative_to(workspace_root).as_posix()
        return {
            name
            for name in names
            if match_stage_exclude(PurePosixPath(relative_directory, name), patterns)
            is not None
        }

    return ignore


def _required_manifests(plan: PublishPlan) -> tuple[tuple[str, Path], ...]:
    """Return each manifest staging must retain, labelled for error messages."""
    manifests: list[tuple[str, Path]] = [
        ("the workspace", plan.workspace_root / "Cargo.toml")
    ]
    manifests.extend(
        (f"crate {crate.name!r}", crate.manifest_path)
        for crate in (
            *plan.publishable,
            *plan.skipped_manifest,
            *plan.skipped_configuration,
        )
    )
    return tuple(manifests)


def _manifest_relative_path(
    manifest: Path, workspace_root: Path
) -> PurePosixPath | None:
    """Return the manifest's path as staging copies it, or :data:`None`.

    The lexical path is used rather than the manifest's resolved target:
    ``copytree`` excludes an entry by the name it walks, so a pattern must be
    checked against that spelling. A symlinked ``Cargo.toml`` resolves
    elsewhere, and matching the target would let a pattern remove the link
    after validation had cleared it.

    Returns
    -------
    PurePosixPath | None
        The workspace-relative manifest path, or :data:`None` when no spelling
        of the workspace root contains the manifest, in which case staging
        never copies it.
    """
    for root in (workspace_root, workspace_root.resolve(strict=False)):
        try:
            return PurePosixPath(manifest.relative_to(root).as_posix())
        except ValueError:
            continue
    return None


def _link_path(path: Path) -> tuple[Path, tuple[Path, ...]]:
    """Return the link's target and the directories its text must traverse.

    The kernel applies a ``..`` in the link text to the physical parent, so the
    parent is resolved before the text is joined. It also visits each component
    in turn: ``../bridge/../manifests`` fails when ``bridge`` is absent even
    though a lexical join names only ``manifests``. Components the resolution
    ends beneath are already covered by matching the target's ancestors, so
    only the components it leaves again are returned as extra dependencies.

    Returns
    -------
    tuple[Path, tuple[Path, ...]]
        The path the link text names, with its parent already resolved, and
        each component the resolution enters but does not end beneath.
    """
    link_text = path.readlink()
    current = (
        Path(link_text.anchor)
        if link_text.is_absolute()
        else path.parent.resolve(strict=False)
    )
    traversed: list[Path] = []
    for part in link_text.parts:
        if part == "..":
            current = current.parent
        elif part in {".", link_text.anchor}:
            continue
        else:
            current /= part
            traversed.append(current)
    diverged = tuple(
        candidate for candidate in traversed if not current.is_relative_to(candidate)
    )
    return current, diverged


def _is_symlink(path: Path) -> bool:
    """Return whether ``path`` is a symbolic link, preserving inspection errors.

    :meth:`Path.is_symlink` reports every :class:`OSError` as ``False``, so a
    link that could not be inspected would be skipped and an exclusion could
    remove its target unnoticed. Only a missing entry or a non-directory
    component counts as "not a link"; any other failure is left to the caller.

    Returns
    -------
    bool
        Whether ``path`` is a symbolic link. An inspection failure the caller
        did not exclude propagates, rather than being reported as ``False``.
    """
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError, NotADirectoryError:
        return False
    return stat.S_ISLNK(mode)


def _link_targets_in_path(path: Path, workspace_root: Path) -> cabc.Iterator[Path]:
    """Yield what each symbolic link in ``path`` or an ancestor names.

    A link to a directory is followed by the rest of the path, because that is
    what the kernel does with ``alias/Cargo.toml`` when ``alias`` names
    ``real``: the manifest staging must preserve is ``real/Cargo.toml``, and a
    pattern removing it would leave the staged link dangling. A directory the
    link text traverses before a later ``..`` counts too, because the kernel
    must enter it even though the final target does not sit beneath it.

    Yields
    ------
    Path
        The target of a link in ``path`` or one of its parent directories,
        joined with the remaining components of ``path``, plus each directory
        the link text leaves again, in path order. A path outside the workspace
        names nothing staging copies, so it yields no targets.
    """
    relative = _manifest_relative_path(path, workspace_root)
    if relative is None:
        return
    for prefix in workspace_relative_prefixes(relative):
        component = workspace_root / prefix
        if _is_symlink(component):
            target, traversed = _link_path(component)
            remainder = relative.parts[len(prefix.parts) :]
            yield from traversed
            yield target.joinpath(*remainder)


def _unseen_link_targets(
    path: Path, workspace_root: Path, seen: set[Path]
) -> tuple[Path, ...]:
    """Return and record each link target of ``path`` not already walked."""
    targets = set(_link_targets_in_path(path, workspace_root)) - seen
    seen.update(targets)
    # Sorted so a rejection names its dependency in a stable order.
    return tuple(sorted(targets))


def _preserved_dependencies(path: Path, workspace_root: Path) -> cabc.Iterator[Path]:
    """Yield ``path`` and every path staging must copy to preserve it.

    Staging copies each symbolic link as a link, so preserving ``path`` needs
    both the link itself and its target to be copied. Walking the links one at
    a time, rather than using :meth:`Path.resolve`, shows each intermediate
    path that staging could prune.

    A link in a parent component counts as much as a link to the file:
    ``alias/workspace.toml`` still depends on whatever ``alias`` names. A cycle
    is abandoned, because it never reaches a file staging could copy.

    Yields
    ------
    Path
        ``path``, then each path reached by a symbolic link in it or in one of
        its ancestors.
    """
    pending = [path]
    seen: set[Path] = {path}
    while pending:
        current = pending.pop()
        yield current
        pending.extend(_unseen_link_targets(current, workspace_root, seen))


def _is_absent(path: Path) -> bool:
    """Return whether ``path`` names nothing staging could copy.

    :meth:`Path.exists` reports every :class:`OSError` as absence, so an
    unreadable directory would hide behind a silently accepted exclusion and
    the run would fail later, during the copy, instead of being reported as an
    inspection failure. Only a missing entry, a non-directory component, or a
    looping link counts as absent; any other failure is left to the caller.

    Returns
    -------
    bool
        Whether ``path`` names nothing staging could copy.

    Raises
    ------
    OSError
        If ``path`` exists but cannot be inspected for a reason other than a
        loop, such as a permission denial.
    """
    try:
        path.stat()
    except FileNotFoundError, NotADirectoryError:
        return True
    except OSError as error:
        if error.errno == errno.ELOOP:
            return True
        raise
    return False


def _dependency_relatives(
    manifest: Path, workspace_root: Path
) -> tuple[PurePosixPath, ...]:
    """Return each link dependency of ``manifest`` inside the workspace.

    A dependency outside the workspace names nothing staging copies, so it is
    omitted: no pattern can remove it from the staged tree.

    Returns
    -------
    tuple[PurePosixPath, ...]
        Each dependency the workspace contains, relative to the workspace
        root.
    """
    if _is_absent(manifest):
        # A broken chain has no target staging could have copied, and a pattern
        # is not what broke it.
        return ()
    relatives: list[PurePosixPath] = []
    for dependency in _preserved_dependencies(manifest, workspace_root):
        relative = _manifest_relative_path(dependency, workspace_root)
        if relative is not None:
            relatives.append(relative)
    return tuple(relatives)


def _reject_excluded_manifests(
    plan: PublishPlan,
    stage_exclude: cabc.Sequence[str],
    *,
    preserve_symlinks: bool,
) -> None:
    """Raise for the first required manifest ``stage_exclude`` would remove."""
    for label, manifest in _required_manifests(plan):
        relative = _manifest_relative_path(manifest, plan.workspace_root)
        if relative is None:
            # No spelling of the workspace root contains the manifest, so
            # staging never copies it and no pattern can remove it.
            continue
        message = excluded_manifest_message(label, relative, stage_exclude)
        if message is None and preserve_symlinks:
            message = dangling_dependency_message(
                label,
                relative,
                _dependency_relatives(manifest, plan.workspace_root),
                stage_exclude,
            )
        if message is not None:
            raise PublishPreparationError(message)


def _validate_stage_exclude(
    plan: PublishPlan,
    stage_exclude: cabc.Sequence[str],
    *,
    preserve_symlinks: bool,
) -> None:
    """Reject exclusions that would remove a manifest staging must package.

    Checked before any staging directory exists, so a configuration that
    cannot work fails without creating or copying a tree first.

    If an exclusion removes a required manifest, the delegated
    `_reject_excluded_manifests` call raises `PublishPreparationError`.

    Parameters
    ----------
    plan : PublishPlan
        The publication whose manifests staging must retain.
    stage_exclude : cabc.Sequence[str]
        Normalized exclusion patterns.
    preserve_symlinks : bool
        Whether staging copies symbolic links as links. When it does, a
        required manifest that is a link is unusable if a pattern removes the
        link's target inside the workspace: the staged link would dangle.

    Raises
    ------
    StageExcludeInspectionError
        If the workspace cannot be read to decide the question. It is a
        :class:`PublishPreparationError`, so callers still see a staging
        failure, but it is reported apart from a refused pattern.
    """
    if not stage_exclude:
        return
    try:
        # A pattern matching a required manifest raises a
        # `PublishPreparationError` from here; this function only adds the
        # translation of an unreadable workspace around it.
        _reject_excluded_manifests(
            plan, stage_exclude, preserve_symlinks=preserve_symlinks
        )
    except OSError as error:
        # A link that cannot be read is an environment failure rather than a
        # bad pattern. Translating it here keeps raw filesystem errors from
        # escaping the staging boundary.
        message = (
            "could not inspect the workspace to validate "
            f"publish.stage_exclude: {error}"
        )
        raise StageExcludeInspectionError(message) from error
