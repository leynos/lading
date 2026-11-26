"""Options and outcome evidence shared by atomic TOML writers."""

import dataclasses as dc
from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class AtomicWriteIdentity:
    """Identify one filesystem object across atomic replacement.

    Attributes
    ----------
    device : int
        Device number reported when the object was inspected.
    inode : int
        Inode number reported when the object was inspected.
    """

    device: int
    inode: int


@dc.dataclass(slots=True)
class AtomicWriteStatus:
    """Record whether an atomic writer replaced its destination.

    Attributes
    ----------
    replacement_completed : bool, default False
        Whether the writer returned after installing the replacement. Each
        attempt resets this before writing.
    replacement_identity : AtomicWriteIdentity or None, default None
        Identity of the temporary file that was installed. The writer
        publishes it immediately before the rename, so a non-``None`` value
        can also describe a replacement interrupted between the rename and the
        completion flag. Each attempt resets it before writing.
    """

    replacement_completed: bool = False
    replacement_identity: AtomicWriteIdentity | None = None


@dc.dataclass(frozen=True, slots=True)
class AtomicWriteOptions:
    """Pair workspace constraints with replacement and lock evidence.

    Attributes
    ----------
    workspace_root : Path or None, optional
        Workspace boundary for a bounded replacement. ``None`` selects the
        unbounded path-based writer.
    workspace_identity : AtomicWriteIdentity or None, optional
        Identity captured with the workspace lock. A bounded writer verifies
        that the opened workspace directory still has this identity. Callers
        normally leave this unset; the atomic writer supplies it after
        acquiring the lock.
    expected_current_text : str or None, optional
        Exact destination text required immediately before replacement.
        Requires ``workspace_root``.
    expected_current_identity : AtomicWriteIdentity or None, optional
        Identity the destination must still have immediately before
        replacement. Unlike ``replacement_identity``, this describes the file
        being replaced rather than the temporary file. Requires
        ``workspace_root``.
    status : AtomicWriteStatus or None, optional
        Mutable record populated by the writer. When supplied, the writer
        resets it before writing, publishes the replacement identity before
        the rename, and marks completion afterwards.
    """

    workspace_root: Path | None = None
    workspace_identity: AtomicWriteIdentity | None = None
    expected_current_text: str | None = None
    expected_current_identity: AtomicWriteIdentity | None = None
    status: AtomicWriteStatus | None = None
