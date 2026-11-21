"""Apply already-planned edits to configured non-member Cargo manifests.

This adapter checks that each source still matches its plan, then writes the
replacement atomically. The check protects a plan from overwriting edits made
between planning and application.
"""

import collections.abc as cabc
from pathlib import Path

from lading.commands.bump_manifest_rewrite_model import (
    ManifestRewriteError,
    ManifestRewritePlan,
)
from lading.commands.bump_toml_files import write_atomic_text


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
        If a manifest changed after planning or a write fails. The message
        identifies the failed manifest and how many planned writes completed.

    Notes
    -----
    Each atomic write protects one manifest. If a later write fails, earlier
    completed writes remain in place, so the sequence is not atomic as a whole.
    """
    if plans and not dry_run:
        for completed_writes, plan in enumerate(plans):
            try:
                current_text = plan.path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                message = (
                    f"Could not read manifest rewrite target {plan.path} before "
                    f"writing: {exc}"
                )
                raise ManifestRewriteError(
                    message,
                    completed_writes=completed_writes,
                    planned_writes=len(plans),
                    failed_path=plan.path,
                ) from exc
            if current_text != plan.original_text:
                message = f"Manifest rewrite target changed after planning: {plan.path}"
                raise ManifestRewriteError(
                    message,
                    completed_writes=completed_writes,
                    planned_writes=len(plans),
                    failed_path=plan.path,
                )
            try:
                write_atomic_text(plan.path, plan.new_text)
            except (OSError, UnicodeError) as exc:
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
