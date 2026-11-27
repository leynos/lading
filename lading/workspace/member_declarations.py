"""Declare and validate the workspace members named by the root manifest.

Cargo expands ``[workspace].members`` with globs, applies literal
``[workspace].exclude`` prefixes, and reads each member's manifest. This module
performs the same declaration work with confinement: patterns are validated
lexically, expanded without following intermediate links, exclusions are applied
as literal prefixes, and every accepted match holds a confined regular
``Cargo.toml`` before Cargo runs.

The module is bump-only and holds no command state.
"""

import collections.abc as cabc
import dataclasses as dc
import fnmatch
import os
import typing as typ
from pathlib import Path, PurePosixPath

from lading.utils.confinement import (
    BumpTargetExaminationError,
    ConfinedPath,
    GlobOptions,
    MatchExpectation,
    MissingBumpTargetError,
    UnmatchedPolicy,
    UnsafeBumpTargetError,
    confine_relative_path,
    expand_confined_glob,
    validate_relative_pattern,
)

__all__ = ["expand_member_patterns"]

_ROOT_MANIFEST = "Cargo.toml"
_MEMBERS_KEY = "workspace.members"
_EXCLUDES_KEY = "workspace.exclude"


def expand_member_patterns(
    root: Path,
    members: cabc.Sequence[str],
    excludes: cabc.Sequence[str],
) -> dict[tuple[str, ...], str]:
    """Expand member patterns, apply exclusions, and return declared members.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    members : cabc.Sequence[str]
        Literal member paths or globs, expanded case-sensitively as Cargo does.
    excludes : cabc.Sequence[str]
        Literal path prefixes; Cargo does not expand wildcards here.

    Returns
    -------
    dict[tuple[str, ...], str]
        Member directory components mapped to the pattern that declared them.

    Raises
    ------
    BumpPreflightError
        If a pattern is unsafe, a literal member is missing, a glob matches
        nothing, a matched directory is not a real directory, or a matched
        member has no regular ``Cargo.toml``.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by expand_confined_glob
    excluded = _expand_exclusions(excludes)
    declared: dict[tuple[str, ...], str] = {}
    for index, pattern in enumerate(members):
        key = f"{_MEMBERS_KEY}[{index}]"
        if not _is_root_member(pattern):
            _reject_hidden_wildcard(pattern, key=key)
        is_glob = bool(_GLOB_METACHARACTERS.intersection(pattern))
        declarations = _MemberDeclarations(root=root, pattern=pattern, key=key)
        for match in _member_matches(root, pattern, key=key):
            excluded_match = is_glob and _is_excluded(match.parts, excluded)
            declarations.declare(match, is_excluded=excluded_match)
        declared.update(declarations.declared)
    return declared


@dc.dataclass(slots=True)
class _MemberDeclarations:
    """Accumulate the matches of one member pattern."""

    root: Path
    pattern: str
    key: str
    declared: dict[tuple[str, ...], str] = dc.field(default_factory=dict)

    def declare(self, match: ConfinedPath, *, is_excluded: bool) -> None:
        """Record one match, or examine an excluded one and skip it."""
        if is_excluded:
            _examine_excluded_member(self.root, match, key=self.key)
            return
        _require_member_manifest(self.root, match, key=self.key, pattern=self.pattern)
        self.declared[match.parts] = self.pattern


_GLOB_METACHARACTERS: typ.Final[frozenset[str]] = frozenset("*?[")


def _reject_hidden_wildcard(pattern: str, *, key: str) -> None:
    """Reject a member component that starts with ``.`` and holds a wildcard.

    Cargo's glob crate can turn such a component into ``..``; Cargo would then
    open a manifest outside the workspace before the reported-member check.

    Raises
    ------
    UnsafeBumpTargetError
        If a component combines a leading dot with a wildcard.
    """
    for part in validate_relative_pattern(pattern, key=key):
        literal = _parent_selector(part)
        if literal is None:
            continue
        message = f"{key}: member pattern component can select {literal!r}: {part!r}"
        raise UnsafeBumpTargetError(message)


def _parent_selector(part: str) -> str | None:
    """Return ``.`` or ``..`` when ``part`` can select a parent directory.

    Cargo matches a wildcard component against the whole directory name, so a
    pattern such as ``.[a-z]*`` cannot select ``.`` or ``..`` and is accepted.

    Returns
    -------
    str | None
        The special entry the component actually matches, or ``None``.
    """
    if not _GLOB_METACHARACTERS.intersection(part) or part == "**":
        return None
    for special in (".", ".."):
        if not part.startswith("."):
            # A wildcard component without a literal leading dot never selects
            # a dot entry: directory iteration never yields ``.`` or ``..``.
            continue
        if fnmatch.fnmatchcase(special, part):
            return special
    return None


def _examine_excluded_member(
    root: Path,
    match: ConfinedPath,
    *,
    key: str,
) -> None:
    """Validate an excluded match's manifest when one already exists.

    Cargo still parses an excluded directory's manifest when an explicit member
    covers it, so a symlink or special file there must be rejected before the
    metadata call. An excluded directory without a manifest is not an error.

    Raises
    ------
    BumpTargetExaminationError
        If the manifest cannot be examined.
    UnsafeBumpTargetError
        If the manifest is a symlink or is not a regular file.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by confinement
    manifest = match.path / _ROOT_MANIFEST
    try:
        os.lstat(manifest)
    except FileNotFoundError:
        return
    except OSError as exc:
        message = f"{key}: cannot examine {match.display}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    pattern = PurePosixPath(*match.parts, _ROOT_MANIFEST).as_posix()
    confine_relative_path(root, pattern, key=key)


def _member_matches(
    root: Path,
    pattern: str,
    *,
    key: str,
) -> tuple[ConfinedPath, ...]:
    """Expand one member pattern, accepting an explicit ``"."`` for the root."""
    if _is_root_member(pattern):
        return (ConfinedPath(root=root, parts=()),)
    return expand_confined_glob(
        root,
        pattern,
        key=key,
        options=GlobOptions(
            expect=MatchExpectation.DIRECTORY,
            unmatched=UnmatchedPolicy.ERROR,
            reject_intermediate_links=True,
            case_sensitive=True,
        ),
    )


def _is_root_member(pattern: str) -> bool:
    """Return whether ``pattern`` explicitly names the workspace root.

    Whitespace is significant: ``" . "`` names a directory whose name contains
    spaces, while ``"."`` and ``"./"`` name the root itself. ``Path`` collapses
    dot components but preserves everything else.

    Returns
    -------
    bool
        ``True`` when the pattern leaves no component below the root.
    """
    if not pattern.strip():
        return False
    return Path(pattern).parts == ()


def _is_excluded(
    member_parts: tuple[str, ...],
    excluded: cabc.Set[tuple[str, ...]],
) -> bool:
    """Return whether ``member_parts`` is ``excluded`` or lies beneath it.

    Cargo applies an exclusion to a matching directory and its descendants;
    an exact comparison would leave ``crates/retired/app`` a member when
    ``crates/retired`` is excluded. Explicitly listed members keep Cargo's
    precedence, so only glob-derived matches are filtered.

    Returns
    -------
    bool
        ``True`` when the member is excluded or lies beneath an exclusion.
    """
    return any(
        member_parts[: len(excluded_parts)] == excluded_parts
        for excluded_parts in excluded
    )


def _expand_exclusions(
    excludes: cabc.Sequence[str],
) -> set[tuple[str, ...]]:
    """Return the literal directory components excluded from the member set.

    Cargo treats ``exclude`` entries as literal path prefixes and does not
    expand wildcards there, so a wildcard character names a directory with that
    character in its name. No filesystem access is needed: an exclusion may
    name a directory that does not exist.

    Returns
    -------
    set[tuple[str, ...]]
        Excluded directory component tuples.
    """
    return {
        validate_relative_pattern(pattern, key=f"{_EXCLUDES_KEY}[{index}]")
        for index, pattern in enumerate(excludes)
    }


def _require_member_manifest(
    root: Path,
    match: ConfinedPath,
    *,
    key: str,
    pattern: str,
) -> None:
    """Require ``match`` to contain a confined regular ``Cargo.toml``."""
    manifest_pattern = PurePosixPath(*match.parts, _ROOT_MANIFEST).as_posix()
    try:
        confine_relative_path(root, manifest_pattern, key=key)
    except MissingBumpTargetError as exc:
        message = (
            f"{key}: workspace member {match.display!r} matched by "
            f"{pattern!r} has no Cargo.toml"
        )
        raise MissingBumpTargetError(message) from exc
