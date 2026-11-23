"""Non-following glob expansion beneath a confined workspace root.

This module is the policy-facing entry point. It validates the pattern, applies
the unmatched and directory-selector policy, and proves canonical containment
for every match. The recursive engine lives in
:mod:`lading.utils.confinement.glob_expansion`; the policy types live in
:mod:`lading.utils.confinement.glob_types`.

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
import os
import typing as typ
from pathlib import Path, PurePosixPath

from lading.utils.confinement.glob_expansion import _Expansion
from lading.utils.confinement.glob_types import (
    GlobOptions,
    MatchExpectation,
    UnmatchedPolicy,
)
from lading.utils.confinement.patterns import validate_relative_pattern
from lading.utils.confinement.roots import ProofContext, prove_containment
from lading.utils.confinement.walk import ConfinedPath, MissingBumpTargetError

__all__ = ["GlobOptions", "MatchExpectation", "UnmatchedPolicy", "expand_confined_glob"]

# Windows treats a backslash as a separator; POSIX allows it in a filename.
_TRAILING_SEPARATORS: typ.Final[tuple[str, ...]] = (
    ("/", "\\") if os.sep == "\\" else ("/",)
)


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
    if _is_directory_selector(pattern, key=key, policy=policy):
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
    return _contained_matches(root, key, expansion.found)


def _is_directory_selector(
    pattern: str,
    *,
    key: str,
    policy: GlobOptions,
) -> bool:
    """Return whether a trailing separator makes this a no-match selector.

    Documentation patterns select regular files, so a directory selector
    matches nothing; it must not fall back to selecting files directly under
    the directory.

    Returns
    -------
    bool
        Whether the pattern is a directory selector that must produce no
        matches.

    Raises
    ------
    MissingBumpTargetError
        If the policy requires a match and the selector matches nothing.
    """
    if policy.expect is not MatchExpectation.REGULAR_FILE:
        return False
    if not pattern.endswith(_TRAILING_SEPARATORS):
        return False
    if policy.unmatched is UnmatchedPolicy.ERROR:
        message = f"{key}: pattern matched nothing: {pattern!r}"
        raise MissingBumpTargetError(message)
    return True


def _contained_matches(
    root: Path,
    key: str,
    found: cabc.Iterable[tuple[str, ...]],
) -> tuple[ConfinedPath, ...]:
    """Return contained matches for the expansion's component sets."""
    matches: list[ConfinedPath] = []
    for matched_parts in sorted(found):
        display = PurePosixPath(*matched_parts).as_posix()
        prove_containment(
            root,
            root.joinpath(*matched_parts),
            ProofContext(key=key, display=display),
        )
        matches.append(ConfinedPath(root=root, parts=matched_parts))
    return tuple(matches)
