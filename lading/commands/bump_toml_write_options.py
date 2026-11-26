"""Options and outcome evidence shared by atomic TOML writers."""

import dataclasses as dc
from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class AtomicWriteIdentity:
    """Identify one filesystem object across atomic replacement."""

    device: int
    inode: int


@dc.dataclass(slots=True)
class AtomicWriteStatus:
    """Record whether an atomic writer replaced its destination."""

    replacement_completed: bool = False
    replacement_identity: AtomicWriteIdentity | None = None


@dc.dataclass(frozen=True, slots=True)
class AtomicWriteOptions:
    """Pair workspace constraints with replacement and lock evidence."""

    workspace_root: Path | None = None
    workspace_identity: AtomicWriteIdentity | None = None
    expected_current_text: str | None = None
    expected_current_identity: AtomicWriteIdentity | None = None
    status: AtomicWriteStatus | None = None
