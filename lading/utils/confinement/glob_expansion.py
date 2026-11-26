"""Recursive, non-following pattern expansion for confined globs.

The engine expands one component at a time with :func:`os.scandir` and
:func:`fnmatch.fnmatch`. ``**`` matches the current directory and zero or more
descendant directories and never follows a link. Literal and wildcard
components reject symlink matches, special files, and (for file patterns)
directory matches. :mod:`lading.utils.confinement.glob` owns the public entry
point and re-exports this engine for its tests.
"""

import collections.abc as cabc
import dataclasses as dc
import fnmatch
import os
import stat
import typing as typ
from pathlib import Path, PurePosixPath

from lading.utils.confinement.glob_types import (
    MatchExpectation,
    UnmatchedPolicy,
)
from lading.utils.confinement.walk import (
    BumpTargetExaminationError,
    LinkPredicate,
    UnsafeBumpTargetError,
    display_relative,
)

__all__ = ["_GLOB_METACHARACTERS", "_Expansion"]

_GLOB_METACHARACTERS: typ.Final[frozenset[str]] = frozenset("*?[")


@dc.dataclass(slots=True)
class _Expansion:
    """State for one recursive, non-following pattern expansion."""

    root: Path
    key: str
    expect: MatchExpectation
    unmatched: UnmatchedPolicy
    link_predicate: LinkPredicate
    found: set[tuple[str, ...]] = dc.field(default_factory=set)
    matched_any: bool = False

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
            self._select(result, matched_parts, wants_regular=wants_regular)
            return
        ignored = is_directory if wants_regular else is_regular
        if ignored:
            if not wants_regular and is_regular:
                # A member glob that matches only regular files has matched
                # something; the unmatched policy must not reject the pattern.
                self.matched_any = True
            return
        self._reject_match(
            matched_parts[:-1], matched_parts[-1], "matched a special file"
        )

    def _select(
        self,
        result: os.stat_result,
        matched_parts: tuple[str, ...],
        *,
        wants_regular: bool,
    ) -> None:
        """Record one selected match, rejecting hard-linked regular files."""
        if wants_regular:
            self._reject_hard_link(result, matched_parts)
        self.found.add(matched_parts)

    def _reject_hard_link(
        self,
        result: os.stat_result,
        matched_parts: tuple[str, ...],
    ) -> None:
        """Reject a selected regular file with more than one hard link."""
        if result.st_nlink > 1:
            # A write through one name would leave the other names stale, so
            # hard-linked files follow the single-target walk's policy.
            self._reject_match(
                matched_parts[:-1],
                matched_parts[-1],
                "hard-linked target is not permitted",
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
