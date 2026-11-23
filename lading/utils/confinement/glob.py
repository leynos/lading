"""Non-following glob expansion beneath a confined workspace root.

This module expands documentation and workspace-member patterns one component
at a time with :func:`os.scandir` and :func:`fnmatch.fnmatch`. The recursive
``**`` component matches the current directory and zero or more descendant
directories, and it never follows a symlink. Literal and wildcard components
reject symlink matches, special files, and (for file patterns) directory
matches.

Expansion is the discovery half of the bump confinement policy; the walking and
error vocabulary live in :mod:`lading.utils.confinement.walk` and
:mod:`lading.utils.confinement.errors`.

Examples
--------
>>> import tempfile
>>> from pathlib import Path
>>> from lading.utils.confinement import establish_workspace_root
>>> with tempfile.TemporaryDirectory() as directory:
...     root = establish_workspace_root(directory)
...     _ = (root / "docs").mkdir()
...     _ = (root / "docs" / "guide.md").write_text("", encoding="utf-8")
...     [match.display for match in expand_confined_glob(
...         root, "docs/*.md", key="bump.documentation.globs"
...     )]
['docs/guide.md']
"""

import collections.abc as cabc
import dataclasses as dc
import enum
import fnmatch
import os
import stat
import typing as typ
from pathlib import Path, PurePosixPath

from lading.utils.confinement.patterns import validate_relative_pattern
from lading.utils.confinement.roots import ProofContext, prove_containment
from lading.utils.confinement.walk import (
    BumpTargetExaminationError,
    ConfinedPath,
    LinkPredicate,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
    display_relative,
    is_link_like,
)

__all__ = [
    "GlobOptions",
    "MatchExpectation",
    "UnmatchedPolicy",
    "expand_confined_glob",
]

_GLOB_METACHARACTERS: typ.Final[frozenset[str]] = frozenset("*?[")


class MatchExpectation(enum.Enum):
    """How a glob should treat final matches of each kind.

    ``REGULAR_FILE`` selects documentation files: regular files are returned
    and directory matches are ignored. ``DIRECTORY`` selects workspace-member
    directories: directories are returned and regular-file matches are ignored,
    matching Cargo's own member glob expansion. In both modes a matched special
    file is an error.
    """

    REGULAR_FILE = enum.auto()
    DIRECTORY = enum.auto()


class UnmatchedPolicy(enum.Enum):
    """What an expansion does when a pattern matches nothing."""

    IGNORE = enum.auto()
    ERROR = enum.auto()


@dc.dataclass(frozen=True, slots=True)
class GlobOptions:
    """Matching policy for :func:`expand_confined_glob`.

    Attributes
    ----------
    expect : MatchExpectation
        Kind of final match to select.
    unmatched : UnmatchedPolicy
        Whether a pattern that matches nothing is an error.
    link_predicate : LinkPredicate
        Predicate identifying symlinks and junctions.
    """

    expect: MatchExpectation = MatchExpectation.REGULAR_FILE
    unmatched: UnmatchedPolicy = UnmatchedPolicy.IGNORE
    link_predicate: LinkPredicate = is_link_like


def expand_confined_glob(
    root: Path,
    pattern: str,
    *,
    key: str,
    options: GlobOptions | None = None,
) -> tuple[ConfinedPath, ...]:
    """Expand ``pattern`` beneath ``root`` without following links.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    pattern : str
        Workspace-relative glob. ``**`` matches zero or more directories, as in
        :meth:`pathlib.Path.glob`, but a symlinked directory is never descended
        into.
    key : str
        Configuration key or manifest field naming the pattern in diagnostics.
    options : GlobOptions | None
        Matching policy. ``None`` selects documentation files, treats an
        unmatched pattern as a no-op, and uses the portable link test.

    Returns
    -------
    tuple[ConfinedPath, ...]
        Sorted, de-duplicated matches. Each is proved contained beneath
        ``root``.

    Raises
    ------
    UnsafeBumpTargetError
        If the pattern is unsafe, a matched component is a symlink, a special
        file is matched, or canonical containment fails.
    BumpTargetExaminationError
        If scanning a directory or examining an entry fails.
    MissingBumpTargetError
        If ``unmatched`` is ``ERROR`` and nothing matched.
    """  # ruff: ignore[docstring-extraneous-exception]  # delegated to _Expansion
    parts = validate_relative_pattern(pattern, key=key)
    policy = options or GlobOptions()
    if pattern.endswith(("/", "\\")) and policy.expect is MatchExpectation.REGULAR_FILE:
        # A trailing separator selects directories. Documentation patterns
        # select regular files, so a directory selector matches nothing; it
        # must not fall back to selecting files directly under the directory.
        if policy.unmatched is UnmatchedPolicy.ERROR:
            message = f"{key}: pattern matched nothing: {pattern!r}"
            raise MissingBumpTargetError(message)
        return ()
    expansion = _Expansion(
        root=root,
        key=key,
        expect=policy.expect,
        unmatched=policy.unmatched,
        link_predicate=policy.link_predicate,
    )
    expansion.expand(parts, prefix=())
    if not expansion.found and policy.unmatched is UnmatchedPolicy.ERROR:
        message = f"{key}: pattern matched nothing: {pattern!r}"
        raise MissingBumpTargetError(message)
    matches: list[ConfinedPath] = []
    for matched_parts in sorted(expansion.found):
        display = PurePosixPath(*matched_parts).as_posix()
        prove_containment(
            root,
            root.joinpath(*matched_parts),
            ProofContext(key=key, display=display),
        )
        matches.append(ConfinedPath(root=root, parts=matched_parts))
    return tuple(matches)


@dc.dataclass(slots=True)
class _Expansion:
    """State for one recursive, non-following pattern expansion."""

    root: Path
    key: str
    expect: MatchExpectation
    unmatched: UnmatchedPolicy
    link_predicate: LinkPredicate
    found: set[tuple[str, ...]] = dc.field(default_factory=set)

    def expand(self, remaining: tuple[str, ...], prefix: tuple[str, ...]) -> None:
        """Expand ``remaining`` starting from the directory ``prefix``.

        Every caller passes at least one component: a final component selects
        through :meth:`_consider_final`, and the directory itself is handled by
        :meth:`_consider_current_directory`.
        """
        component, rest = remaining[0], remaining[1:]
        if component == "**":
            self._expand_recursive(remaining, prefix)
        elif _GLOB_METACHARACTERS.intersection(component):
            self._expand_wildcard(component, rest, prefix)
        else:
            self._expand_literal(component, rest, prefix)

    def _scandir(self, directory: Path) -> list[os.DirEntry[str]] | None:
        """Return directory entries, or ``None`` when the directory is absent."""
        try:
            with os.scandir(directory) as entries:
                return list(entries)
        except FileNotFoundError:
            return None
        except OSError as exc:
            message = (
                f"{self.key}: cannot list directory "
                f"{display_relative(self.root, directory)}: {exc}"
            )
            raise BumpTargetExaminationError(message) from exc

    def _entry_stat(self, entry: os.DirEntry[str]) -> os.stat_result:
        """Return one entry's ``lstat`` result, reporting scan failures."""
        try:
            return entry.stat(follow_symlinks=False)
        except OSError as exc:
            message = (
                f"{self.key}: cannot examine match "
                f"{display_relative(self.root, Path(entry.path))}: {exc}"
            )
            raise BumpTargetExaminationError(message) from exc

    def _iter_entries(
        self,
        prefix: tuple[str, ...],
        component: str,
    ) -> cabc.Iterator[tuple[str, os.stat_result]]:
        """Yield matching entry names and their ``lstat`` results."""
        entries = self._scandir(self.root.joinpath(*prefix))
        if entries is None:
            return
        for entry in entries:
            if not fnmatch.fnmatch(entry.name, component):
                continue
            yield entry.name, self._entry_stat(entry)

    def _expand_recursive(
        self,
        remaining: tuple[str, ...],
        prefix: tuple[str, ...],
    ) -> None:
        """Expand a ``**`` component without following symlinks."""
        is_final = len(remaining) == 1
        if is_final:
            self._consider_current_directory(prefix)
        else:
            # ``**`` matches the current directory as zero levels of descent.
            self.expand(remaining[1:], prefix)
        for name, result in self._iter_entries(prefix, "*"):
            if self.link_predicate(result):
                if is_final:
                    self._reject_match(
                        prefix, name, "symlinked pattern match is not permitted"
                    )
                # An intermediate symlink cannot be traversed, so it contributes
                # no match. Raising here would reject unrelated links, such as
                # an image symlink under a documentation tree.
                continue
            if stat.S_ISDIR(result.st_mode):
                self.expand(remaining, (*prefix, name))
            elif is_final:
                self._consider_final(result, (*prefix, name))

    def _consider_current_directory(self, prefix: tuple[str, ...]) -> None:
        """Apply the final expectation to the directory ``prefix`` itself."""
        if not prefix:
            return
        candidate = self.root.joinpath(*prefix)
        try:
            result = os.lstat(candidate)
        except OSError as exc:
            message = (
                f"{self.key}: cannot examine target "
                f"{display_relative(self.root, candidate)}: {exc}"
            )
            raise BumpTargetExaminationError(message) from exc
        self._consider_final(result, prefix)

    def _expand_wildcard(
        self,
        component: str,
        rest: tuple[str, ...],
        prefix: tuple[str, ...],
    ) -> None:
        """Expand a component containing ``*``, ``?``, or ``[``."""
        for matched in self._iter_entries(prefix, component):
            self._descend(rest, prefix, matched)

    def _expand_literal(
        self,
        component: str,
        rest: tuple[str, ...],
        prefix: tuple[str, ...],
    ) -> None:
        """Expand a literal component, which must not traverse a symlink."""
        candidate = self.root.joinpath(*prefix, component)
        try:
            result = os.lstat(candidate)
        except FileNotFoundError:
            # One branch produced no match. The caller raises only when the
            # whole expansion is empty, so a wildcard branch that matched
            # elsewhere is not rejected here.
            return
        except OSError as exc:
            message = (
                f"{self.key}: cannot examine target "
                f"{display_relative(self.root, candidate)}: {exc}"
            )
            raise BumpTargetExaminationError(message) from exc
        self._descend(rest, prefix, (component, result))

    def _descend(
        self,
        rest: tuple[str, ...],
        prefix: tuple[str, ...],
        matched: tuple[str, os.stat_result],
    ) -> None:
        """Reject a linked match, then select it or descend into it."""
        name, result = matched
        if self.link_predicate(result):
            self._reject_match(prefix, name, "symlinked pattern match is not permitted")
        if not rest:
            self._consider_final(result, (*prefix, name))
        elif stat.S_ISDIR(result.st_mode):
            self.expand(rest, (*prefix, name))
        elif not stat.S_ISREG(result.st_mode):
            self._reject_match(prefix, name, "matched a special file")

    def _consider_final(
        self,
        result: os.stat_result,
        matched_parts: tuple[str, ...],
    ) -> None:
        """Select or reject one final match according to ``expect``.

        Cargo expands `[workspace].members` globs to directories and ignores
        non-directory matches, so a stray file under `crates/` must not fail a
        workspace Cargo already accepts. Symlinks were rejected before this
        point, and a matched special file is still an error: it is neither a
        documentation file nor a member directory, and silently ignoring it
        would hide a misconfigured pattern.

        Raises
        ------
        UnsafeBumpTargetError
            If a matched special file is neither a selected file nor an ignored
            directory.
        """  # ruff: ignore[docstring-extraneous-exception]  # raised by _raise_special_file
        is_regular = stat.S_ISREG(result.st_mode)
        is_directory = stat.S_ISDIR(result.st_mode)
        wants_regular = self.expect is MatchExpectation.REGULAR_FILE
        selected = is_regular if wants_regular else is_directory
        if selected:
            if wants_regular and result.st_nlink > 1:
                # A write through one name would leave the other names stale,
                # so hard-linked files follow the single-target walk's policy.
                self._reject_match(
                    matched_parts[:-1],
                    matched_parts[-1],
                    "hard-linked target is not permitted",
                )
            self.found.add(matched_parts)
            return
        ignored = is_directory if wants_regular else is_regular
        if ignored:
            return
        self._reject_match(
            matched_parts[:-1], matched_parts[-1], "matched a special file"
        )

    def _reject_match(
        self,
        prefix: tuple[str, ...],
        name: str,
        detail: str,
    ) -> typ.NoReturn:
        """Raise the unsafe-target error for one rejected match.

        Parameters
        ----------
        prefix : tuple[str, ...]
            Components of the directory holding the match.
        name : str
            Matched entry name.
        detail : str
            Reason included in the diagnostic.

        Raises
        ------
        UnsafeBumpTargetError
            Always, naming the rejected match and the reason.
        """
        display = PurePosixPath(*prefix, name).as_posix()
        message = f"{self.key}: {detail}: {display}"
        raise UnsafeBumpTargetError(message)
