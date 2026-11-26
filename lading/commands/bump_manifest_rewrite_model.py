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
        Number of manifests persisted before or by a write failure. Defaults
        to zero.
    planned_writes : int, optional
        Number of manifests in the write plan. Defaults to zero.
    failed_path : Path or None, optional
        Manifest associated with the error, or ``None`` when no single path is
        known.
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

    @classmethod
    def pre_bump_versions_unavailable(
        cls,
        crate_names: cabc.Sequence[str],
        manifest_path: Path,
    ) -> ManifestRewriteError:
        """Create an error for selected rewrites missing source versions.

        Parameters
        ----------
        crate_names : Sequence[str]
            Workspace packages whose pre-bump versions were not available.
        manifest_path : Path
            Configured manifest whose rewrite could not be planned.

        Returns
        -------
        ManifestRewriteError
            Failure identifying the missing package versions and manifest.
        """
        names = ", ".join(repr(name) for name in crate_names)
        message = (
            f"Pre-bump versions for workspace crate(s) {names} are unavailable "
            f"while rewriting {manifest_path}"
        )
        return cls(message, failed_path=manifest_path)


class ManifestRewriteWriteError(ManifestRewriteError):
    """Report an atomic write failure with its partial progress.

    This subtype lets the CLI reserve structured progress events for failures
    raised by an attempted atomic write.

    Attributes
    ----------
    completed_writes : int
        Number of manifests persisted before this failure.
    planned_writes : int
        Number of manifests in the write plan.
    failed_path : Path or None
        Manifest whose atomic write failed.
    """


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
