"""Domain-facing port for reconciling files with a GitHub release."""

import collections.abc as cabc
import pathlib
import typing as typ


class ReleaseOperationError(RuntimeError):
    """Raised when a release asset operation cannot be completed."""


class ReleaseAssetStore(typ.Protocol):
    """Read attached filenames and publish distributions to one release."""

    def attached_names(self, tag: str) -> cabc.Collection[str]:
        """Return the filenames attached to ``tag``."""

    def upload_missing(
        self, tag: str, distributions: cabc.Sequence[pathlib.Path]
    ) -> tuple[pathlib.Path, ...]:
        """Upload the supplied distributions and return those uploaded."""
