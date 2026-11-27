"""Workspace-root establishment and canonical containment proof.

These helpers are separate from the component walk so each module stays focused
and within the repository's file-size limit. They are re-exported from the
package root.
"""

import dataclasses as dc
import os
import stat
from pathlib import Path

from lading.utils.confinement.errors import (
    BumpTargetExaminationError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)

__all__ = [
    "ProofContext",
    "establish_workspace_root",
    "prove_containment",
    "relative_to_root",
]


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
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by helpers
    candidate = _candidate_root(value)
    resolved = _resolve_root(candidate)
    try:
        status = resolved.stat()
    except OSError as exc:
        message = f"Cannot examine workspace root {candidate}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    if not stat.S_ISDIR(status.st_mode):
        message = f"Workspace root is not a directory: {candidate}"
        raise UnsafeBumpTargetError(message)
    return resolved


def _candidate_root(value: Path | str | None) -> Path:
    """Return the unexpanded candidate root, reporting expansion failures."""
    try:
        return Path.cwd() if value is None else Path(value).expanduser()
    except (RuntimeError, OSError) as exc:
        message = f"Cannot expand workspace root {value!r}: {exc}"
        raise BumpTargetExaminationError(message) from exc


def _resolve_root(candidate: Path) -> Path:
    """Return the strictly resolved root, reporting resolution failures."""
    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        message = f"Workspace root does not exist: {candidate}"
        raise MissingBumpTargetError(message) from exc
    except OSError as exc:
        message = f"Cannot examine workspace root {candidate}: {exc}"
        raise BumpTargetExaminationError(message) from exc


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
        If ``candidate`` is on another drive, outside ``root``, resolves
        elsewhere through a symlink, or contains a parent-traversing ``..``.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by helpers
    _require_same_drive(root, candidate, key=key, label=label)
    relative = _lexical_relative(root, candidate, key=key, label=label)
    _require_resolved_containment(candidate, root, key=key, label=label)
    return relative


def _require_same_drive(root: Path, candidate: Path, *, key: str, label: str) -> None:
    """Reject a candidate on a different drive from the root."""
    if not _same_drive(root, candidate):
        message = f"{key}: {label} is on a different drive: {candidate}"
        raise UnsafeBumpTargetError(message)


def _same_drive(root: Path, candidate: Path) -> bool:
    """Return whether both paths name the same drive."""
    return os.path.normcase(os.path.splitdrive(candidate)[0]) == os.path.normcase(
        os.path.splitdrive(root)[0]
    )


def _lexical_relative(root: Path, candidate: Path, *, key: str, label: str) -> Path:
    """Return the lexically root-relative path, rejecting escapes."""
    try:
        # ``relpath`` would collapse ``link/..`` lexically, while the operating
        # system resolves it through ``link``. Preserving the component lets
        # the lexical validator reject the path instead of checking a
        # different file.
        relative = candidate.relative_to(root)
    except ValueError as exc:
        message = f"{key}: {label} is outside the workspace root: {candidate}"
        raise UnsafeBumpTargetError(message) from exc
    if ".." in relative.parts:
        message = f"{key}: {label} contains a parent traversal: {candidate}"
        raise UnsafeBumpTargetError(message)
    return relative


def _require_resolved_containment(
    candidate: Path,
    root: Path,
    *,
    key: str,
    label: str,
) -> None:
    """Reject a lexically contained path that resolves outside the root."""
    if not _is_within_directory_identity(_existing_ancestor(candidate), root):
        message = (
            f"{key}: {label} does not resolve beneath the workspace root "
            f"(a symlink or case-only sibling may be involved): {candidate}"
        )
        raise UnsafeBumpTargetError(message)


def _existing_ancestor(candidate: Path) -> Path:
    """Return ``candidate`` or its nearest existing ancestor."""
    probe = candidate
    while not os.path.lexists(probe) and probe.parent != probe:
        probe = probe.parent
    return probe


@dc.dataclass(frozen=True, slots=True)
class ProofContext:
    """Diagnostic context and creation policy for a containment proof.

    Attributes
    ----------
    key : str
        Configuration key or role naming the target in diagnostics.
    display : str
        Root-relative spelling used in the diagnostic.
    may_create : bool
        Permit an absent final component while still requiring every parent to
        resolve.
    """

    key: str
    display: str
    may_create: bool = False


def prove_containment(
    root: Path,
    candidate: Path,
    context: ProofContext,
) -> None:
    """Prove that ``candidate`` canonically resolves beneath ``root``.

    Resolution is strict so a component that cannot be examined raises instead
    of being appended to a partial path, which would let the proof succeed
    without establishing that every component resolves under the root. A final
    component that is allowed to be created is the one exception.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    candidate : Path
        Absolute candidate target.
    context : ProofContext
        Diagnostic spelling and creation policy.

    Raises
    ------
    UnsafeBumpTargetError
        If the canonical candidate is not inside the canonical root.
    BumpTargetExaminationError
        If a component cannot be resolved.
    """
    try:
        canonical_root = Path(os.path.realpath(root, strict=True))
        if context.may_create:
            # Only the final component may be absent; every parent must be a
            # real, resolvable directory.
            canonical_parent = Path(os.path.realpath(candidate.parent, strict=True))
            canonical_candidate = canonical_parent / candidate.name
            try:
                os.lstat(candidate)
            except FileNotFoundError:
                # Only a genuinely absent final component may be created.
                pass
            else:
                # A dangling final symlink reaches strict resolution here and
                # is reported rather than looking absent.
                canonical_candidate = Path(os.path.realpath(candidate, strict=True))
        else:
            canonical_candidate = Path(os.path.realpath(candidate, strict=True))
    except OSError as exc:
        message = f"{context.key}: cannot resolve target {context.display}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    if not _is_within_directory_identity(canonical_candidate, canonical_root):
        message = (
            f"{context.key}: target resolves outside the workspace root: "
            f"{context.display}"
        )
        raise UnsafeBumpTargetError(message)


def _is_within_directory_identity(candidate: Path, root: Path) -> bool:
    """Return whether ``candidate`` is inside ``root`` by directory identity.

    Path comparison is case-insensitive on Windows, so a canonical path under
    a case-only sibling of the root would pass a lexical ``relative_to`` test.
    Comparing the resolved candidate and its ancestors with ``samefile``
    accepts alternate spellings only when they identify the same directory.

    Returns
    -------
    bool
        ``True`` when the candidate or one of its ancestors is ``root``.
    """
    probe = candidate if os.path.lexists(candidate) else candidate.parent
    try:
        resolved = Path(os.path.realpath(probe, strict=True))
    except OSError:
        return False
    for ancestor in (resolved, *resolved.parents):
        try:
            if ancestor.samefile(root):
                return True
        except OSError:
            continue
    return False
