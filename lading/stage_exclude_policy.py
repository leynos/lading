"""Spelling rules for workspace-relative staging exclusions.

``publish.stage_exclude`` reaches staging by two routes: a ``lading.toml``
file, and a direct call to :func:`lading.commands.publish_staging.
staged_workspace` or
:func:`~lading.commands.publish_staging.prepare_workspace`. Both routes apply
the spelling rules here, so a spelling that can never match a copied path is
reported instead of silently excluding nothing.

The routes differ on one input. A file is read through
:func:`lading.toml_coerce.string_tuple`, which turns a bare string into a
one-pattern list, so ``stage_exclude = "target"`` means one pattern. A direct
caller reaches :func:`normalize_stage_exclude` unchanged, where a bare string
is rejected: a ``str`` is a sequence of characters, and iterating it would
splinter the pattern instead of applying it.

This module depends on neither the configuration nor the command layer, so
both can use it without an import cycle. The configuration loader translates
:class:`StageExcludeError` into :class:`~lading.config.ConfigurationError` for
a bad file; the staging boundary translates it into
:class:`~lading.commands.publish_manifest.PublishPreparationError`.

Pattern matching and the protection of required manifests also belong here.
Both answer questions about workspace-relative paths, so they accept
:class:`pathlib.PurePosixPath` values and either return a value or raise
:class:`StageExcludeError`; neither reads the filesystem. The staging adapter
in :mod:`lading.commands.publish_stage_exclude` gathers those paths, walks
symbolic links, and translates filesystem errors.
"""

import collections.abc as cabc
import fnmatch
from pathlib import PurePosixPath, PureWindowsPath

#: Prefix used in error messages when a caller names no field.
_FIELD_NAME = "publish.stage_exclude"


class StageExcludeError(ValueError):
    """A staging exclusion pattern cannot describe a workspace path."""


def _is_blank(candidate: str) -> bool:
    """Return whether the pattern is empty or whitespace only."""
    return not candidate.strip()


def _is_negated(candidate: str) -> bool:
    """Return whether the pattern uses gitignore's ``!`` negation."""
    # A gitignore habit rather than a request staging can honour: the pattern
    # would load, match only a file literally named '!…', and exclude nothing
    # while the reader believes it re-included a path.
    return candidate.startswith("!")


def _is_absolute(candidate: str) -> bool:
    """Return whether the pattern is rooted in either path flavour."""
    return candidate.startswith("/") or bool(PureWindowsPath(candidate).drive)


def _uses_backslashes(candidate: str) -> bool:
    """Return whether the pattern separates segments with a backslash."""
    return "\\" in candidate


#: Spellings that would load but never match a copied entry, with the reason
#: reported. Order decides which reason a multi-fault pattern reports.
_SPELLING_REJECTIONS: tuple[tuple[cabc.Callable[[str], bool], str], ...] = (
    (_is_blank, "must not be blank"),
    (_is_negated, "must not start with '!' because negation is not supported"),
    (_is_absolute, "must be relative to the workspace root"),
    (_uses_backslashes, "must use '/' separators"),
)


def _reject_unusable_spelling(
    candidate: str, field_name: str, original: object
) -> None:
    """Reject a spelling no relative workspace path can have."""
    for is_unusable, reason in _SPELLING_REJECTIONS:
        if is_unusable(candidate):
            message = f"{field_name} {reason}; received {original!r}."
            raise StageExcludeError(message)


def _reject_non_workspace_segments(
    candidate: str, field_name: str, original: object
) -> None:
    """Reject segments that would load but never match a copied entry."""
    path = PurePosixPath(candidate)
    if ".." in path.parts:
        message = f"{field_name} must not contain '..'; received {original!r}."
        raise StageExcludeError(message)
    if candidate == "." or candidate != path.as_posix():
        # ``as_posix()`` never renders a bare '.' or an empty segment, so such
        # a pattern would load and then match nothing.
        message = (
            f"{field_name} must not contain '.' or empty path segments; "
            f"received {original!r}."
        )
        raise StageExcludeError(message)


def _coerce_pattern(pattern: object, entry_name: str) -> str:
    """Return ``pattern`` as a string, rejecting any other type."""
    match pattern:
        case str():
            return pattern
        case _:
            message = f"{entry_name} must be a string; received {pattern!r}."
            raise StageExcludeError(message)


def normalize_stage_exclude(
    patterns: cabc.Sequence[object], *, field_name: str = _FIELD_NAME
) -> tuple[str, ...]:
    """Return ``patterns`` normalized, rejecting unusable spellings.

    Parameters
    ----------
    patterns : cabc.Sequence[object]
        Candidate patterns, normally strings. A non-string is rejected rather
        than coerced, because a coerced value has no obvious intent.
    field_name : str
        Base name for error messages. The pattern's index is appended, so a
        rejection names the offending position.

    Returns
    -------
    tuple[str, ...]
        The patterns, each with one trailing slash removed.

    Raises
    ------
    StageExcludeError
        If ``patterns`` is a bare string, or a pattern is not a string, is
        blank, starts with ``!``, is absolute, uses backslashes, or contains
        ``..``, ``.`` or an empty segment.

    Examples
    --------
    >>> normalize_stage_exclude([".git", "target/"])
    ('.git', 'target')
    >>> normalize_stage_exclude([])
    ()
    """
    match patterns:
        case str():
            # A str is a Sequence[str], so a type checker accepts a single
            # pattern here; iterating it would splinter "target" per letter.
            message = (
                f"{field_name} must be a sequence of patterns, not a single "
                f"string; received {patterns!r}."
            )
            raise StageExcludeError(message)
    normalized: list[str] = []
    for index, pattern in enumerate(patterns):
        entry_name = f"{field_name}[{index}]"
        candidate = _coerce_pattern(pattern, entry_name).removesuffix("/")
        _reject_unusable_spelling(candidate, entry_name, pattern)
        _reject_non_workspace_segments(candidate, entry_name, pattern)
        normalized.append(candidate)
    return tuple(normalized)


def workspace_relative_prefixes(
    relative_path: PurePosixPath,
) -> tuple[PurePosixPath, ...]:
    """Return ``relative_path`` and every ancestor below the workspace root.

    Returns
    -------
    tuple[PurePosixPath, ...]
        The ancestors from the shallowest to the deepest, ending with
        ``relative_path`` itself.

    Examples
    --------
    >>> workspace_relative_prefixes(PurePosixPath("a/b/c"))
    (PurePosixPath('a'), PurePosixPath('a/b'), PurePosixPath('a/b/c'))
    """
    ancestors = tuple(
        parent for parent in reversed(relative_path.parents) if parent.parts
    )
    return (*ancestors, relative_path)


def match_stage_exclude(
    relative_path: PurePosixPath, patterns: cabc.Sequence[str]
) -> str | None:
    """Return the first exclusion pattern matching ``relative_path``, if any.

    A pattern without a slash names a basename at any depth, the way a
    gitignore entry without a slash does, and a leading ``**/`` is accepted as
    the same intent rather than as a pattern that requires a leading directory.
    A pattern with a slash is matched against the whole workspace-relative
    path, and its ``*`` may span separators. A leading ``**/`` lifts that
    anchoring: the remainder matches at any depth, including the workspace
    root. An interior ``**`` is not special, so it still needs a separator on
    each side and does not make the segment it names optional.

    Parameters
    ----------
    relative_path : PurePosixPath
        The copied path, relative to the workspace root.
    patterns : cabc.Sequence[str]
        Normalized exclusion patterns.

    Returns
    -------
    str | None
        The matching pattern as configured, or :data:`None` when no pattern
        matches.

    Examples
    --------
    >>> match_stage_exclude(PurePosixPath("tools/venv"), ("venv",))
    'venv'
    >>> match_stage_exclude(PurePosixPath("crates/app/src"), ("crates/*",))
    'crates/*'
    >>> match_stage_exclude(PurePosixPath("src/lib.rs"), ("venv",)) is None
    True
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


def exclusion_of(
    relative_path: PurePosixPath, patterns: cabc.Sequence[str]
) -> tuple[PurePosixPath, str] | None:
    """Return the path and pattern excluding ``relative_path`` or an ancestor.

    Excluding a directory removes everything below it, so a pattern matching
    an ancestor removes ``relative_path`` as surely as one matching the path
    itself.

    Returns
    -------
    tuple[PurePosixPath, str] | None
        ``relative_path`` and the first matching pattern, or :data:`None` when
        the path survives ``patterns``.

    Examples
    --------
    >>> exclusion_of(PurePosixPath("crates/app/src"), ("crates/app",))
    (PurePosixPath('crates/app/src'), 'crates/app')
    """
    for candidate in workspace_relative_prefixes(relative_path):
        pattern = match_stage_exclude(candidate, patterns)
        if pattern is not None:
            return relative_path, pattern
    return None


def excluded_manifest_message(
    label: str, manifest_relative: PurePosixPath, patterns: cabc.Sequence[str]
) -> str | None:
    """Return why ``patterns`` would remove a required manifest, if they would.

    Returns
    -------
    str | None
        A message naming the pattern and the manifest, or :data:`None` when
        the manifest survives ``patterns``.
    """
    exclusion = exclusion_of(manifest_relative, patterns)
    if exclusion is None:
        return None
    pattern = exclusion[1]
    return (
        f"publish.stage_exclude pattern {pattern!r} would exclude "
        f"{label} manifest {manifest_relative.as_posix()!r}"
    )


def dangling_dependency_message(
    label: str,
    manifest_relative: PurePosixPath,
    dependencies: cabc.Sequence[PurePosixPath],
    patterns: cabc.Sequence[str],
) -> str | None:
    """Return why an excluded link dependency breaks ``manifest_relative``.

    A manifest reached through a symbolic link needs both the link and its
    target copied. Excluding the target leaves the staged link dangling, even
    though no pattern matches the manifest's own path.

    Parameters
    ----------
    label : str
        How the manifest is named in an error message.
    manifest_relative : PurePosixPath
        The manifest's workspace-relative path.
    dependencies : cabc.Sequence[PurePosixPath]
        The link targets the manifest depends on, workspace-relative.
    patterns : cabc.Sequence[str]
        Normalized exclusion patterns.

    Returns
    -------
    str | None
        A message naming the excluded dependency, or :data:`None` when every
        dependency survives ``patterns``.
    """
    for dependency in dependencies:
        exclusion = exclusion_of(dependency, patterns)
        if exclusion is not None:
            pattern = exclusion[1]
            return (
                f"publish.stage_exclude pattern {pattern!r} would exclude "
                f"{label} manifest {manifest_relative.as_posix()!r}, which "
                f"depends on symlink target {dependency.as_posix()!r} that "
                f"staging would not copy"
            )
    return None
