"""Workspace-root establishment and canonical containment proof.

These helpers are separate from the component walk so each module stays focused
and within the repository's file-size limit. They are re-exported from the
package root.
"""

import dataclasses as dc
import os
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
    """
    try:
        candidate = Path.cwd() if value is None else Path(value).expanduser()
    except RuntimeError as exc:
        message = f"Cannot expand workspace root {value!r}: {exc}"
        raise BumpTargetExaminationError(message) from exc
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
    strict_mode = os.path.ALLOW_MISSING if context.may_create else True
    try:
        canonical_root = Path(os.path.realpath(root, strict=strict_mode))
        canonical_candidate = Path(os.path.realpath(candidate, strict=strict_mode))
    except OSError as exc:
        message = f"{context.key}: cannot resolve target {context.display}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    try:
        canonical_candidate.relative_to(canonical_root)
    except ValueError as exc:
        message = (
            f"{context.key}: target resolves outside the workspace root: "
            f"{context.display}"
        )
        raise UnsafeBumpTargetError(message) from exc
