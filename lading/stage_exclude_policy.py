"""Spelling rules for workspace-relative staging exclusions.

``publish.stage_exclude`` reaches staging by two routes: a ``lading.toml``
file, and a direct call to :func:`lading.commands.publish_staging.
staged_workspace` or
:func:`~lading.commands.publish_staging.prepare_workspace`. Both routes apply
the rules here, so a pattern is accepted or rejected the same way whichever
one it arrives by, and a spelling that can never match a copied path is
reported instead of silently excluding nothing.

This module depends on neither the configuration nor the command layer, so
both can use it without an import cycle. The configuration loader translates
:class:`StageExcludeError` into :class:`~lading.config.ConfigurationError` for
a bad file; the staging boundary translates it into
:class:`~lading.commands.publish_manifest.PublishPreparationError`. Pattern
matching and the protection of required manifests live in
:mod:`lading.commands.publish_stage_exclude`.
"""

import collections.abc as cabc
from pathlib import PurePosixPath, PureWindowsPath

#: Prefix used in error messages when a caller names no field.
_FIELD_NAME = "publish.stage_exclude"


class StageExcludeError(ValueError):
    """A staging exclusion pattern cannot describe a workspace path."""


def _reject_unusable_spelling(
    candidate: str, field_name: str, original: object
) -> None:
    """Reject a spelling no relative workspace path can have."""
    if not candidate.strip():
        message = f"{field_name} must not be blank; received {original!r}."
        raise StageExcludeError(message)
    if candidate.startswith("!"):
        # A gitignore habit rather than a request staging can honour: the
        # pattern would load, match only a file literally named '!…', and
        # exclude nothing while the reader believes it re-included a path.
        message = (
            f"{field_name} must not start with '!' because negation is not "
            f"supported; received {original!r}."
        )
        raise StageExcludeError(message)
    if candidate.startswith("/") or PureWindowsPath(candidate).drive:
        message = (
            f"{field_name} must be relative to the workspace root; "
            f"received {original!r}."
        )
        raise StageExcludeError(message)
    if "\\" in candidate:
        message = f"{field_name} must use '/' separators; received {original!r}."
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
