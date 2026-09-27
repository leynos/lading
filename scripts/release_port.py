"""The uploader's process port: what the policy needs, without cuprum.

``release_wheel_upload`` decides what to upload and must be testable with no
process and no cuprum present. ``release_gh`` holds the process edge and the
only cuprum import in the uploader. This module sits between them and holds the
type they agree on, so the dependency arrow points at the port rather than
through the adapter: the policy imports this, the adapter imports this, and
neither imports the other.

The division matters because the alternative -- defining the return type in the
adapter, as the uploader did before -- makes the policy's type-level dependency
point at the one module that imports cuprum, which is the arrangement a port
exists to prevent. This module therefore imports nothing but the standard
library, and must continue to.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ


@dc.dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What a ``gh`` invocation reported back.

    This is the whole of the command dependency's return contract, so a test
    runner can satisfy it without a process. It is frozen because a caller
    reads it as a record of what happened, not as state to amend.
    """

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class UploadRunner(typ.Protocol):
    """Runs one ``gh`` invocation and reports how it went."""

    def __call__(self, arguments: cabc.Sequence[str]) -> CommandOutcome:
        """Run ``gh`` with ``arguments`` and return its outcome."""
