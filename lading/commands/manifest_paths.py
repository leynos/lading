"""Share path expansion, containment, and filename checks for Cargo manifests.

:func:`expand_manifest_glob_candidates` expands rewrite globs while propagating
directory-scan errors. :func:`resolve_manifest_candidate` checks that a
resolved candidate remains inside its workspace and is named ``Cargo.toml``.
The lockfile path resolver in :mod:`lading.commands.bump_lockfile_paths` and
the rewrite planner in :mod:`lading.commands.bump_manifest_rewrites` supply
their own error types; each caller retains responsibility for existence and
regular-file checks.
"""

import dataclasses as dc
import fnmatch
import glob
import os
import stat
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from lading.exceptions import LadingError


def resolve_manifest_candidate(
    workspace_root: Path,
    manifest: str | Path,
    *,
    error_type: type[LadingError],
    label: str,
) -> Path:
    r"""Resolve one Cargo manifest after enforcing workspace and filename rules.

    Parameters
    ----------
    workspace_root : Path
        Workspace directory that owns the candidate manifest.
    manifest : str | Path
        Workspace-relative path or resolved glob candidate to validate.
    error_type : type[LadingError]
        Domain error class used for unsafe paths and invalid filenames.
    label : str
        Subject used to distinguish validation errors for each caller.

    Returns
    -------
    Path
        Resolved path to the candidate manifest.

    Raises
    ------
    LadingError
        The requested ``error_type`` when the path escapes the workspace or
        does not end in ``Cargo.toml``.

    Examples
    --------
    >>> from lading.exceptions import LadingError
    >>> from pathlib import Path
    >>> options = {"error_type": LadingError, "label": "Manifest"}
    >>> root = Path("/workspace")
    >>> candidate = resolve_manifest_candidate(
    ...     root, "fixtures/demo/Cargo.toml", **options
    ... )
    >>> candidate.relative_to(root.resolve()).as_posix()
    'fixtures/demo/Cargo.toml'
    """  # ruff: ignore[docstring-extraneous-exception]  # error_type selects the raised subclass
    candidate = (workspace_root / manifest).resolve()
    try:
        candidate.relative_to(workspace_root.resolve())
    except ValueError as exc:
        message = f"{label} path must stay within the workspace: {manifest}"
        raise error_type(message) from exc
    if candidate.name != "Cargo.toml":
        message = f"{label} path must point to a Cargo.toml file: {manifest}"
        raise error_type(message)
    return candidate


def expand_manifest_glob_candidates(
    workspace_root: Path,
    pattern: str,
    *,
    error_type: type[LadingError],
    label: str,
) -> tuple[Path, ...]:
    r"""Expand a workspace-relative manifest glob without suppressing scan errors.

    Parameters
    ----------
    workspace_root : Path
        Workspace directory that owns the selected manifests.
    pattern : str
        Relative path pattern containing glob metacharacters.
    error_type : type[LadingError]
        Domain error class used when the pattern escapes the workspace.
    label : str
        Subject used in containment errors.

    Returns
    -------
    tuple[Path, ...]
        Sorted matching candidates. The caller validates their filenames and
        file types.

    Notes
    -----
    ``Path.glob`` suppresses scanning ``OSError`` exceptions on Python 3.13+
    and can therefore return an incomplete result. This segment traversal
    keeps the same wildcard matching while allowing scan failures to propagate.
    An escaping path raises ``error_type``; scan failures retain their original
    ``OSError``.
    """
    traversal = _ManifestGlobTraversal(
        workspace_root=workspace_root,
        pattern=pattern,
        error_type=error_type,
        label=label,
    )
    return traversal.collect()


@dc.dataclass(slots=True)
class _ManifestGlobTraversal:
    """Own the filesystem scan state for one configured manifest glob."""

    workspace_root: Path
    pattern: str
    error_type: type[LadingError]
    label: str
    root: Path = dc.field(init=False)
    segments: tuple[str, ...] = dc.field(init=False)
    directory_only: bool = dc.field(init=False)
    matches: list[Path] = dc.field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize the resolved root and path segments before traversal."""
        self.root = self.workspace_root.resolve()
        segments = Path(self.pattern).parts
        self.segments = tuple(
            segment
            for index, segment in enumerate(segments)
            if segment != "**" or index == 0 or segments[index - 1] != "**"
        )
        self.directory_only = self.pattern.endswith(os.sep) or (
            os.altsep is not None and self.pattern.endswith(os.altsep)
        )

    def collect(self) -> tuple[Path, ...]:
        """Return all matching candidates in stable path order."""
        self.visit(self.workspace_root, 0)
        return tuple(sorted(self.matches))

    def visit_recursive(self, candidate: Path, index: int) -> None:
        """Match ``**`` without following directory symlinks, avoiding cycles."""
        self.visit(candidate, index + 1)
        is_terminal_recursive = index == len(self.segments) - 1
        for child in _manifest_directory_entries(candidate):
            if _is_manifest_directory(child):
                self.visit(child, index)
            elif is_terminal_recursive and not self.directory_only:
                self.record_if_present(child)

    def visit_wildcard(self, candidate: Path, segment: str, index: int) -> None:
        """Match one wildcard path segment against each directory entry."""
        for child in _manifest_directory_entries(candidate):
            if fnmatch.fnmatch(child.name, segment):
                self.visit_candidate(child, index + 1)

    def visit_candidate(self, candidate: Path, index: int) -> None:
        """Skip non-directories when another pattern segment needs traversal."""
        if index < len(self.segments) and not _is_manifest_directory(
            candidate, follow_symlinks=True
        ):
            return
        self.visit(candidate, index)

    def visit(self, candidate: Path, index: int) -> None:
        """Walk one pattern segment while enforcing the workspace boundary."""
        try:
            candidate.resolve().relative_to(self.root)
        except ValueError as exc:
            message = (
                f"{self.label} path must stay within the workspace: {self.pattern}"
            )
            raise self.error_type(message) from exc

        if index == len(self.segments):
            self.record_if_present(candidate)
            return

        segment = self.segments[index]
        if segment == "**":
            self.visit_recursive(candidate, index)
            return

        if glob.has_magic(segment):
            self.visit_wildcard(candidate, segment, index)
            return

        self.visit_candidate(candidate / segment, index + 1)

    def record_if_present(self, candidate: Path) -> None:
        """Record a candidate that still exists after traversal."""
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError, NotADirectoryError:
            return
        if self.directory_only and not stat.S_ISDIR(candidate_stat.st_mode):
            return
        self.matches.append(candidate)


def _manifest_directory_entries(directory: Path) -> tuple[Path, ...]:
    """List one directory, treating vanished or non-directory paths as no match."""
    try:
        return tuple(sorted(directory.iterdir()))
    except FileNotFoundError, NotADirectoryError:
        return ()


def _is_manifest_directory(path: Path, *, follow_symlinks: bool = False) -> bool:
    """Check directory status with optional final-component symlink following."""
    try:
        metadata = path.stat() if follow_symlinks else path.lstat()
        return stat.S_ISDIR(metadata.st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False
