"""Exclusion policy for the staged publish workspace copy.

Staging copies the workspace before packaging, so entries that packaging never
reads only cost time: build output, Git metadata, virtual environments and tool
caches. This module owns the pattern semantics, the :func:`shutil.copytree`
callback that prunes an excluded directory instead of walking it, and the check
that an exclusion cannot remove a manifest the publication needs.

Patterns are filesystem paths relative to the workspace root, not crate names;
``publish.exclude`` is the way to skip a crate from publication. A pattern
without ``/`` matches a basename at any depth, and a leading ``**/`` is accepted
as a spelling of that same intent. A pattern with ``/`` matches the whole
workspace-relative path, where ``*`` may span separators; a leading ``**/``
lifts that anchoring so the remainder matches at any depth, including the
workspace root.
"""

import collections.abc as cabc
import fnmatch
import typing as typ
from pathlib import Path, PurePosixPath

from lading.commands.publish_manifest import PublishPreparationError

if typ.TYPE_CHECKING:
    from lading.commands.publish_plan import PublishPlan


def _match_stage_exclude(
    relative_path: PurePosixPath, patterns: cabc.Sequence[str]
) -> str | None:
    """Return the first exclusion pattern matching ``relative_path``, if any.

    A pattern without a slash names a basename at any depth, the way a
    gitignore entry without a slash does, and a leading ``**/`` is accepted as
    the same intent rather than as a pattern that requires a leading directory.
    A pattern with a slash is matched against the whole workspace-relative
    path, and its ``*`` may span separators. A leading ``**/`` lifts that
    anchoring: the remainder matches at any depth, including the workspace
    root.

    Returns
    -------
    str | None
        The matching pattern as configured, or :data:`None` when no pattern
        matches.
    """
    rendered = relative_path.as_posix()
    for pattern in patterns:
        effective = pattern.removeprefix("**/")
        if "/" not in effective:
            matched = fnmatch.fnmatchcase(relative_path.name, effective)
        else:
            # ``*`` spans separators, so this covers every ancestor depth the
            # remainder may sit below; the anchored spelling covers the root.
            matched = fnmatch.fnmatchcase(rendered, effective) or (
                effective != pattern and fnmatch.fnmatchcase(rendered, f"*/{effective}")
            )
        if matched:
            return pattern
    return None


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
            if _match_stage_exclude(PurePosixPath(relative_directory, name), patterns)
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


def _workspace_relative_prefixes(
    relative_path: PurePosixPath,
) -> tuple[PurePosixPath, ...]:
    """Return ``relative_path`` and every ancestor below the workspace root."""
    ancestors = tuple(
        parent for parent in reversed(relative_path.parents) if parent.parts
    )
    return (*ancestors, relative_path)


def _validate_stage_exclude(
    plan: PublishPlan, stage_exclude: cabc.Sequence[str]
) -> None:
    """Reject exclusions that would remove a manifest staging must package.

    Checked before any staging directory exists, so a configuration that
    cannot work fails without creating or copying a tree first.

    Raises
    ------
    PublishPreparationError
        If a pattern matches a required manifest or one of its ancestor
        directories.
    """
    if not stage_exclude:
        return
    for label, manifest in _required_manifests(plan):
        relative = _manifest_relative_path(manifest, plan.workspace_root)
        if relative is None:
            # A member outside the workspace root is not copied into staging at
            # all, so a workspace-relative pattern cannot remove it.
            continue
        for candidate in _workspace_relative_prefixes(relative):
            pattern = _match_stage_exclude(candidate, stage_exclude)
            if pattern is not None:
                message = (
                    f"publish.stage_exclude pattern {pattern!r} would exclude "
                    f"{label} manifest {relative.as_posix()!r}"
                )
                raise PublishPreparationError(message)
