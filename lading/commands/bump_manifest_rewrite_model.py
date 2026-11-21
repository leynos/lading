"""Neutral records shared by manifest rewrite planning and filesystem edges."""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

from lading.exceptions import LadingError

if typ.TYPE_CHECKING:
    from lading.config import StringValueRewriteConfig


class ManifestRewriteError(LadingError):
    """Report that a configured manifest rewrite cannot be planned or written.

    Parameters
    ----------
    message : str
        The operator-facing explanation of the rewrite failure.
    completed_writes : int, optional
        Number of manifests written before a write failure. Defaults to zero.
    planned_writes : int, optional
        Number of manifests in the write plan. Defaults to zero.
    failed_path : Path or None, optional
        Manifest whose write failed, or ``None`` for planning errors.
    """

    def __init__(
        self,
        message: str,
        *,
        completed_writes: int = 0,
        planned_writes: int = 0,
        failed_path: Path | None = None,
    ) -> None:
        """Initialize an operator-facing failure and write-progress details."""
        super().__init__(message)
        self.completed_writes = completed_writes
        self.planned_writes = planned_writes
        self.failed_path = failed_path


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteVersions:
    """Version context shared by dependency and string rewrite passes.

    Attributes
    ----------
    updated_crate_names : Collection[str]
        Workspace crates selected for this bump.
    pre_bump_versions : Mapping[str, str]
        Current versions used to find old text in configured string values.
    target_version : str
        Version assigned to matching dependency and string references.
    """

    updated_crate_names: cabc.Collection[str]
    pre_bump_versions: cabc.Mapping[str, str]
    target_version: str


@dc.dataclass(frozen=True, slots=True)
class ManifestRewriteSource:
    """Resolved manifest text and rules selected by the filesystem adapter."""

    path: Path
    content: str
    dependencies: bool
    string_values: tuple[StringValueRewriteConfig, ...]


@dc.dataclass(frozen=True, slots=True)
class ManifestRewritePlan:
    """A non-member manifest's original and planned TOML content.

    Attributes
    ----------
    path : Path
        Resolved path to the selected Cargo manifest.
    original_text : str
        TOML content read before any rewrite was applied.
    new_text : str
        Planned TOML content, preserving tomlkit comments and formatting.
    """

    path: Path
    original_text: str
    new_text: str
