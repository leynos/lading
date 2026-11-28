"""Immutable bump write plan and its commit step.

A bump run computes every intended write before it changes anything.
:class:`~lading.commands.bump_plan_builder.PlanBuilder` accumulates those
proposals; this module owns the frozen :class:`BumpPlan`, the deterministic
commit step, and the :class:`BumpChanges` projection.
"""

import collections.abc as cabc
import dataclasses as dc
import enum
import time
import typing as typ
from pathlib import Path

from lading.commands import bump_toml
from lading.commands.bump_manifest_rewrite_model import ManifestRewriteWriteError
from lading.commands.bump_output import BumpChanges

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.commands.bump_lockfile_planning import LockfilePlan
    from lading.commands.bump_preview import DocumentationPreview
    from lading.utils.confinement import ConfinedPath

__all__ = [
    "BumpPlan",
    "PlannedWrite",
    "WriteCategory",
    "changes_from_plan",
    "commit_plan",
]


class WriteCategory(enum.Enum):
    """The role a planned write plays in the bump report."""

    MANIFEST = enum.auto()
    FIXTURE_MANIFEST = enum.auto()
    DOCUMENTATION = enum.auto()
    README = enum.auto()


@dc.dataclass(frozen=True, slots=True)
class PlannedWrite:
    """One file the bump run will write.

    Attributes
    ----------
    path : ConfinedPath
        A path already proved to be inside the workspace and free of symlinked
        components.
    content : str
        The complete text to write.
    categories : frozenset[WriteCategory]
        Every stage that changed this file. A path targeted by both the
        documentation and README stages carries both categories.
    """

    path: ConfinedPath
    content: str
    categories: frozenset[WriteCategory]


@dc.dataclass(frozen=True, slots=True)
class BumpPlan:
    """The complete, validated set of writes for one bump run.

    Attributes
    ----------
    target_version : str
        Version applied by the run, used by the result formatter.
    proposals : tuple[PlannedWrite, ...]
        Writes in commit order.
    lockfiles : LockfilePlan | None
        Planned lockfile regeneration, or ``None`` when regeneration is
        disabled or no manifest changes.
    document_previews : tuple[DocumentationPreview, ...]
        Exact original and proposed text for each documentation rewrite, in
        preparation order.
    """

    target_version: str
    proposals: tuple[PlannedWrite, ...]
    lockfiles: LockfilePlan | None = None
    document_previews: tuple[DocumentationPreview, ...] = ()

    @property
    def manifest_changed(self) -> bool:
        """Whether any workspace or configured fixture manifest is planned.

        Returns
        -------
        bool
            ``True`` when at least one manifest write is proposed.
        """
        manifest_categories = {
            WriteCategory.MANIFEST,
            WriteCategory.FIXTURE_MANIFEST,
        }
        return any(
            manifest_categories & proposal.categories for proposal in self.proposals
        )


def commit_plan(
    plan: BumpPlan,
    *,
    on_write: cabc.Callable[[PlannedWrite, float], None] | None = None,
) -> None:
    """Write every proposal in the plan's deterministic order.

    Parameters
    ----------
    plan : BumpPlan
        A fully validated plan.
    on_write : Callable[[PlannedWrite, float], None] or None, optional
        Observer invoked after each write attempt, successful or failed, with
        the proposal and its elapsed seconds. Defaults to ``None``.

    Raises
    ------
    ManifestRewriteWriteError
        If an atomic write fails, carrying the completed and planned write
        counts and the failed destination.

    Notes
    -----
    Each file is replaced atomically and independently. A failure part-way
    through leaves earlier writes in place; this function deliberately offers
    no rollback, because validation-before-mutation is the guarantee this
    workflow provides. The guarantee applies to the paths as validated: a
    concurrent process that replaces a validated parent with a symlink after
    validation is not defended against.
    """
    planned_writes = len(plan.proposals)
    for completed_writes, proposal in enumerate(plan.proposals):
        started_at = time.perf_counter()
        try:
            bump_toml.write_atomic_text(proposal.path.path, proposal.content)
        except OSError as exc:
            _notify_write(on_write, proposal, started_at)
            message = f"Could not write bump target {proposal.path.path}: {exc}"
            raise ManifestRewriteWriteError(
                message,
                completed_writes=completed_writes,
                planned_writes=planned_writes,
                failed_path=proposal.path.path,
            ) from exc
        _notify_write(on_write, proposal, started_at)


def _notify_write(
    callback: cabc.Callable[[PlannedWrite, float], None] | None,
    proposal: PlannedWrite,
    started_at: float,
) -> None:
    """Report one write attempt to the optional observer."""
    if callback is not None:
        callback(proposal, time.perf_counter() - started_at)


def changes_from_plan(
    plan: BumpPlan,
    *,
    lockfiles: cabc.Sequence[Path] = (),
) -> BumpChanges:
    """Return the change report for ``plan``.

    Parameters
    ----------
    plan : BumpPlan
        The prepared plan.
    lockfiles : cabc.Sequence[Path]
        Lockfiles that were, or would be, regenerated.

    Returns
    -------
    BumpChanges
        Paths grouped by category in the established report order.
    """
    return BumpChanges(
        manifests=_manifest_paths_for(plan),
        documents=_paths_for(plan, WriteCategory.DOCUMENTATION),
        transposed_readmes=tuple(sorted(_paths_for(plan, WriteCategory.README))),
        lockfiles=_root_first(lockfiles),
        document_previews=plan.document_previews,
    )


def _manifest_paths_for(plan: BumpPlan) -> tuple[Path, ...]:
    """Return workspace and configured fixture manifests in commit order."""
    manifest_categories = {
        WriteCategory.MANIFEST,
        WriteCategory.FIXTURE_MANIFEST,
    }
    return tuple(
        proposal.path.path
        for proposal in plan.proposals
        if manifest_categories & proposal.categories
    )


def _root_first(lockfiles: cabc.Sequence[Path]) -> tuple[Path, ...]:
    """Return lockfiles with the workspace-root file first.

    Cargo's execution order is unchanged; only the report is ordered so the
    shallowest destination leads and the rest follow lexicographically,
    regardless of their nesting depth.

    Returns
    -------
    tuple[Path, ...]
        The report order.
    """
    if not lockfiles:
        return ()
    shallowest = min(len(path.parts) for path in lockfiles)
    roots = [path for path in lockfiles if len(path.parts) == shallowest]
    if len(roots) != 1:
        return tuple(sorted(lockfiles, key=Path.as_posix))
    root = roots[0]
    rest = sorted((path for path in lockfiles if path != root), key=Path.as_posix)
    return (root, *rest)


def _paths_for(plan: BumpPlan, category: WriteCategory) -> tuple[Path, ...]:
    """Return the paths in ``plan`` carrying ``category``, in commit order."""
    return tuple(
        proposal.path.path
        for proposal in plan.proposals
        if category in proposal.categories
    )
