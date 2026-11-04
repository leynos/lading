"""Apply planned rewrites to configured non-member Cargo manifests."""

import collections.abc as cabc
import dataclasses as dc
from pathlib import Path

from lading.commands import bump_toml
from lading.exceptions import LadingError


class ManifestRewriteError(LadingError):
    """Report that configured manifest rewrites cannot be planned or written.

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
        """Initialize an operator-facing failure and write-progress details.

        Parameters
        ----------
        message : str
            Explanation of the rewrite failure.
        completed_writes : int, optional
            Number of manifests written before a write failure.
        planned_writes : int, optional
            Number of manifests in the write plan. Defaults to zero.
        failed_path : Path or None, optional
            Manifest whose write failed, or ``None`` for planning errors.
        """
        super().__init__(message)
        self.completed_writes = completed_writes
        self.planned_writes = planned_writes
        self.failed_path = failed_path


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


def apply_manifest_rewrites(
    plans: cabc.Sequence[ManifestRewritePlan],
    *,
    dry_run: bool,
) -> tuple[Path, ...]:
    """Write each planned manifest atomically unless this is a dry run.

    Parameters
    ----------
    plans : Sequence[ManifestRewritePlan]
        Changed non-member manifest plans returned by the rewrite planner.
    dry_run : bool
        When true, report planned paths without writing any files.

    Returns
    -------
    tuple[Path, ...]
        Paths represented by the plans, including during dry runs.

    Raises
    ------
    ManifestRewriteError
        If a write fails. The message identifies the failed manifest and how
        many planned writes completed first.

    Notes
    -----
    Each atomic write protects one manifest. If a later write fails, earlier
    completed writes remain in place, so the sequence is not atomic as a whole.
    """
    if plans and not dry_run:
        for completed_writes, plan in enumerate(plans):
            try:
                bump_toml.write_atomic_text(plan.path, plan.new_text)
            except OSError as exc:
                message = (
                    f"Could not write manifest rewrite target {plan.path} after "
                    f"{completed_writes} of {len(plans)} write(s): {exc}"
                )
                raise ManifestRewriteError(
                    message,
                    completed_writes=completed_writes,
                    planned_writes=len(plans),
                    failed_path=plan.path,
                ) from exc
    return tuple(plan.path for plan in plans)
