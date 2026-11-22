"""Component-wise confinement checks for single bump targets.

The walk inspects every path component below the canonical workspace root with
:func:`os.lstat` before resolving anything. It rejects symlinks, junctions,
non-directory ancestors, and non-regular final components, then proves canonical
containment as an independent second check. The final component is classified
from its ``lstat`` result and never opened, so a FIFO or device cannot block the
command.
"""

import collections.abc as cabc
import dataclasses as dc
import enum
import os
import stat
from pathlib import Path, PurePosixPath

from lading.utils.confinement.errors import (
    BumpTargetExaminationError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from lading.utils.confinement.patterns import validate_relative_pattern

__all__ = [
    "ConfineOptions",
    "ConfinedPath",
    "LinkPredicate",
    "TargetKind",
    "confine_relative_path",
    "establish_workspace_root",
    "is_link_like",
    "prove_containment",
    "relative_to_root",
]

# Windows exposes reparse-point attributes on ``os.stat_result``; CPython on
# POSIX omits the attribute entirely, so the default predicate reduces to the
# portable symlink test there.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

LinkPredicate = cabc.Callable[[os.stat_result], bool]


class TargetKind(enum.Enum):
    """The filesystem kind an intended bump target must have."""

    REGULAR_FILE = enum.auto()
    DIRECTORY = enum.auto()


@dc.dataclass(frozen=True, slots=True)
class ConfinedPath:
    """A path whose physical location is proven to be inside ``root``.

    Attributes
    ----------
    root : Path
        Canonical workspace root.
    parts : tuple[str, ...]
        Lexical components below ``root``. They contain no ``..`` component, and
        no symlink is present at any of them when the value was produced.
    """

    root: Path
    parts: tuple[str, ...]

    @property
    def path(self) -> Path:
        """The absolute path formed from ``root`` and the validated parts.

        Returns
        -------
        Path
            ``root`` joined with the validated components.
        """
        return self.root.joinpath(*self.parts)

    @property
    def relative(self) -> Path:
        """The path relative to the workspace root.

        Returns
        -------
        Path
            The validated components as a relative path.
        """
        return Path(*self.parts)

    @property
    def display(self) -> str:
        """The POSIX spelling used in diagnostics.

        Returns
        -------
        str
            The root-relative path with forward slashes.
        """
        return PurePosixPath(*self.parts).as_posix()


def display_relative(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root`` for diagnostics when possible.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    path : Path
        Absolute path to describe.

    Returns
    -------
    str
        The POSIX relative spelling, or ``str(path)`` when it is outside ``root``.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def is_link_like(result: os.stat_result) -> bool:
    """Return whether an ``lstat`` result is a symlink, junction, or reparse point.

    Parameters
    ----------
    result : os.stat_result
        An ``lstat`` (not ``stat``) result for a path component.

    Returns
    -------
    bool
        ``True`` for a symbolic link or a Windows reparse point.
    """
    if stat.S_ISLNK(result.st_mode):
        return True
    attributes = getattr(result, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def establish_workspace_root(value: Path | str | None) -> Path:
    """Return the canonical workspace root, requiring it to be a directory.

    Parameters
    ----------
    value : Path | str | None
        Candidate root. ``None`` selects the current working directory.

    Returns
    -------
    Path
        The strictly resolved root. Symlinks in the root's own ancestry are
        followed, because a workspace reached through an ancestor symlink is
        still a real workspace.

    Raises
    ------
    MissingBumpTargetError
        If the candidate does not exist.
    BumpTargetExaminationError
        If the candidate cannot be resolved.
    UnsafeBumpTargetError
        If the candidate is not a directory.
    """
    candidate = Path.cwd() if value is None else Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        message = f"Workspace root does not exist: {candidate}"
        raise MissingBumpTargetError(message) from exc
    except OSError as exc:
        message = f"Cannot examine workspace root {candidate}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    if not resolved.is_dir():
        message = f"Workspace root is not a directory: {candidate}"
        raise UnsafeBumpTargetError(message)
    return resolved


@dc.dataclass(frozen=True, slots=True)
class ConfineOptions:
    """Optional policy for :func:`confine_relative_path`.

    Attributes
    ----------
    may_create : bool
        Accept an absent final component when every parent is a real directory.
    expect : TargetKind
        Required kind for an existing final component.
    link_predicate : LinkPredicate
        Predicate identifying symlinks and platform-specific junctions.
    """

    may_create: bool = False
    expect: TargetKind = TargetKind.REGULAR_FILE
    link_predicate: LinkPredicate = is_link_like


def confine_relative_path(
    root: Path,
    pattern: str,
    *,
    key: str,
    options: ConfineOptions | None = None,
) -> ConfinedPath:
    """Return ``pattern`` confined beneath the canonical ``root``.

    Parameters
    ----------
    root : Path
        Canonical workspace root, normally produced by
        :func:`establish_workspace_root`.
    pattern : str
        Workspace-relative path. It must be a plain path; glob expansion uses
        :func:`lading.utils.confinement.expand_confined_glob` instead.
    key : str
        Configuration key or role naming the target in diagnostics.
    options : ConfineOptions | None
        Creation, kind, and link-predicate policy. ``None`` uses the defaults:
        an existing regular file, no creation, and the portable link test.

    Returns
    -------
    ConfinedPath
        The validated path.

    Raises
    ------
    UnsafeBumpTargetError
        If a component is a symlink or junction, an ancestor is not a directory,
        the final component is not the expected kind, or canonical containment
        fails.
    BumpTargetExaminationError
        If a component cannot be examined.
    MissingBumpTargetError
        If a required component does not exist.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     root = establish_workspace_root(directory)
    ...     _ = (root / "Cargo.toml").write_text("", encoding="utf-8")
    ...     confine_relative_path(root, "Cargo.toml", key="manifest").path.name
    'Cargo.toml'
    """  # ruff: ignore[docstring-extraneous-exception]  # delegated to the walk helpers
    walk = _ConfinementWalk(root=root, key=key, options=options or ConfineOptions())
    return walk.confine(pattern)


@dc.dataclass(slots=True)
class _ConfinementWalk:
    """One path walk with a fixed root, diagnostic key, and policy."""

    root: Path
    key: str
    options: ConfineOptions

    def confine(self, pattern: str) -> ConfinedPath:
        """Walk ``pattern`` and return the confined path."""
        parts = validate_relative_pattern(pattern, key=self.key)
        display = PurePosixPath(*parts).as_posix()
        current = self.root
        for index, component in enumerate(parts):
            candidate = current / component
            is_final = index + 1 == len(parts)
            result = self._lstat_component(candidate, is_final=is_final)
            if result is None:
                prove_containment(self.root, candidate, key=self.key, display=display)
                return ConfinedPath(root=self.root, parts=parts)
            self._reject_link_like(result, candidate, display)
            if is_final:
                self._require_final_kind(result, candidate)
            elif not stat.S_ISDIR(result.st_mode):
                message = (
                    f"{self.key}: path component is not a directory: "
                    f"{display_relative(self.root, candidate)}"
                )
                raise UnsafeBumpTargetError(message)
            current = candidate
        prove_containment(self.root, current, key=self.key, display=display)
        return ConfinedPath(root=self.root, parts=parts)

    def _lstat_component(
        self,
        candidate: Path,
        *,
        is_final: bool,
    ) -> os.stat_result | None:
        """Return the ``lstat`` result, or ``None`` for an allowed absent final path."""
        try:
            return os.lstat(candidate)
        except FileNotFoundError as exc:
            if is_final and self.options.may_create:
                return None
            message = (
                f"{self.key}: target does not exist: "
                f"{display_relative(self.root, candidate)}"
            )
            raise MissingBumpTargetError(message) from exc
        except OSError as exc:
            message = (
                f"{self.key}: cannot examine target "
                f"{display_relative(self.root, candidate)}: {exc}"
            )
            raise BumpTargetExaminationError(message) from exc

    def _reject_link_like(
        self,
        result: os.stat_result,
        candidate: Path,
        display: str,
    ) -> None:
        """Reject a symlink or junction at any component of a confined path."""
        if not self.options.link_predicate(result):
            return
        dangling = "" if candidate.exists() else " (dangling link)"
        component = display_relative(self.root, candidate)
        if component == display:
            message = (
                f"{self.key}: symlinked target is not permitted: {display}{dangling}"
            )
        else:
            message = (
                f"{self.key}: symlinked path component is not permitted: "
                f"{component} (target {display}){dangling}"
            )
        raise UnsafeBumpTargetError(message)

    def _require_final_kind(self, result: os.stat_result, candidate: Path) -> None:
        """Reject a final component whose kind differs from the policy."""
        expect = self.options.expect
        is_regular = stat.S_ISREG(result.st_mode)
        is_directory = stat.S_ISDIR(result.st_mode)
        if expect is TargetKind.REGULAR_FILE and is_regular:
            return
        if expect is TargetKind.DIRECTORY and is_directory:
            return
        required = "regular file" if expect is TargetKind.REGULAR_FILE else "directory"
        message = (
            f"{self.key}: target must be a {required}: "
            f"{display_relative(self.root, candidate)}"
        )
        raise UnsafeBumpTargetError(message)


def relative_to_root(root: Path, candidate: Path, *, key: str, label: str) -> Path:
    """Return ``candidate`` relative to ``root``, rejecting anything outside it.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    candidate : Path
        Absolute candidate path, which need not exist.
    key : str
        Configuration key or role naming the subject in diagnostics.
    label : str
        Human-readable description such as ``"manifest"``.

    Returns
    -------
    Path
        The root-relative path.

    Raises
    ------
    UnsafeBumpTargetError
        If ``candidate`` is on another drive or outside ``root``.
    """
    try:
        relative = Path(os.path.relpath(candidate, root))
    except ValueError as exc:
        message = f"{key}: {label} is on a different drive: {candidate}"
        raise UnsafeBumpTargetError(message) from exc
    if relative.parts[:1] == ("..",):
        message = f"{key}: {label} is outside the workspace root: {candidate}"
        raise UnsafeBumpTargetError(message)
    return relative


def prove_containment(root: Path, candidate: Path, *, key: str, display: str) -> None:
    """Prove that ``candidate`` canonically resolves beneath ``root``.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    candidate : Path
        Absolute candidate target.
    key : str
        Configuration key or role naming the target in diagnostics.
    display : str
        Root-relative spelling used in the diagnostic.

    Raises
    ------
    UnsafeBumpTargetError
        If the canonical candidate is not inside the canonical root.
    """
    canonical_root = Path(os.path.realpath(root))
    canonical_candidate = Path(os.path.realpath(candidate))
    try:
        canonical_candidate.relative_to(canonical_root)
    except ValueError as exc:
        message = f"{key}: target resolves outside the workspace root: {display}"
        raise UnsafeBumpTargetError(message) from exc
