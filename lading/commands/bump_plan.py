"""Immutable bump write plan and the proposal builder that produces it.

A bump run computes every intended write before it changes anything. Each
proposal is keyed by its confined absolute path, so two stages that target the
same file compose into one write while retaining both change categories.
Later stages read earlier proposed content through :meth:`PlanBuilder.read`,
which keeps, for example, an adopted README consistent with a documentation
rewrite of the workspace README.

The plan itself is frozen. :func:`commit_plan` walks its proposals in a
deterministic root-manifest, member-manifest, documentation, README order and
performs the only workspace mutations in the bump workflow.
"""

import collections.abc as cabc
import dataclasses as dc
import enum
import typing as typ
from pathlib import Path, PurePosixPath

from lading.commands import bump_toml
from lading.commands.bump_output import BumpChanges
from lading.utils.confinement import (
    BumpTargetExaminationError,
    ConfinedPath,
)

if typ.TYPE_CHECKING:
    from lading.commands.bump_lockfile_planning import LockfilePlan

__all__ = [
    "BumpPlan",
    "PlanBuilder",
    "PlannedWrite",
    "WriteCategory",
    "changes_from_plan",
    "commit_plan",
]

_ROOT_MANIFEST_PARTS: typ.Final[tuple[str, ...]] = ("Cargo.toml",)


class WriteCategory(enum.Enum):
    """The role a planned write plays in the bump report."""

    MANIFEST = enum.auto()
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
    """

    target_version: str
    proposals: tuple[PlannedWrite, ...]
    lockfiles: LockfilePlan | None = None

    @property
    def manifest_changed(self) -> bool:
        """Whether any manifest is in the plan.

        Returns
        -------
        bool
            ``True`` when at least one manifest write is proposed.
        """
        return any(
            WriteCategory.MANIFEST in proposal.categories for proposal in self.proposals
        )


@dc.dataclass(slots=True)
class PlanBuilder:
    """Accumulate proposed writes, composing repeated targets.

    Attributes
    ----------
    root : Path
        Canonical workspace root shared by every confined path.
    """

    root: Path
    _contents: dict[tuple[str, ...], str] = dc.field(default_factory=dict)
    _categories: dict[tuple[str, ...], set[WriteCategory]] = dc.field(
        default_factory=dict
    )
    _order: list[tuple[str, ...]] = dc.field(default_factory=list)

    def read(self, path: ConfinedPath) -> str | None:
        """Return the current content of ``path`` honouring earlier proposals.

        Parameters
        ----------
        path : ConfinedPath
            A confined workspace path.

        Returns
        -------
        str | None
            The proposed content when a stage already changed the file, the
            on-disk text when it exists, or ``None`` when it does not exist.

        Raises
        ------
        BumpTargetExaminationError
            If the file exists but cannot be read or decoded.
        """
        key = path.parts
        proposed = self._contents.get(key)
        if proposed is not None:
            return proposed
        try:
            return path.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            message = f"cannot read bump target {path.display}: {exc}"
            raise BumpTargetExaminationError(message) from exc

    def propose(
        self,
        path: ConfinedPath,
        content: str,
        category: WriteCategory,
    ) -> None:
        """Record ``content`` for ``path`` unless it is already current.

        Parameters
        ----------
        path : ConfinedPath
            A confined workspace path.
        content : str
            The complete text the stage wants the file to contain.
        category : WriteCategory
            The stage's role for reporting purposes.
        """
        key = path.parts
        if key in self._contents:
            self._contents[key] = content
            self._categories[key].add(category)
            return
        if self.read(path) == content:
            return
        self._contents[key] = content
        self._categories[key] = {category}
        self._order.append(key)

    def has_changes(self, category: WriteCategory) -> bool:
        """Return whether any proposed write carries ``category``.

        Parameters
        ----------
        category : WriteCategory
            The category to look for.

        Returns
        -------
        bool
            ``True`` when at least one proposal carries the category.
        """
        return any(category in categories for categories in self._categories.values())

    def build(
        self,
        *,
        target_version: str,
        lockfiles: LockfilePlan | None,
    ) -> BumpPlan:
        """Return the frozen plan in deterministic commit order.

        Parameters
        ----------
        target_version : str
            Version applied by the run.
        lockfiles : LockfilePlan | None
            Planned lockfile regeneration, if any.

        Returns
        -------
        BumpPlan
            The immutable write plan.
        """
        ordered = sorted(self._order, key=self._rank)
        proposals = tuple(
            PlannedWrite(
                path=ConfinedPath(root=self.root, parts=key),
                content=self._contents[key],
                categories=frozenset(self._categories[key]),
            )
            for key in ordered
        )
        return BumpPlan(
            target_version=target_version,
            proposals=proposals,
            lockfiles=lockfiles,
        )

    def _rank(self, key: tuple[str, ...]) -> tuple[int, str]:
        """Return the commit rank for one proposed path."""
        categories = self._categories[key]
        display = PurePosixPath(*key).as_posix()
        if WriteCategory.MANIFEST in categories:
            # The workspace manifest is written before member manifests.
            return (0 if key == _ROOT_MANIFEST_PARTS else 1, display)
        if WriteCategory.DOCUMENTATION in categories:
            return (2, display)
        return (3, display)


def commit_plan(plan: BumpPlan) -> None:
    """Write every proposal in the plan's deterministic order.

    Parameters
    ----------
    plan : BumpPlan
        A fully validated plan.

    Notes
    -----
    Each file is replaced atomically and independently. A failure part-way
    through leaves earlier writes in place; this function deliberately offers
    no rollback, because validation-before-mutation is the guarantee this
    workflow provides.
    """
    for proposal in plan.proposals:
        bump_toml.write_atomic_text(proposal.path.path, proposal.content)


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
        manifests=_paths_for(plan, WriteCategory.MANIFEST),
        documents=_paths_for(plan, WriteCategory.DOCUMENTATION),
        transposed_readmes=_paths_for(plan, WriteCategory.README),
        lockfiles=tuple(lockfiles),
    )


def _paths_for(plan: BumpPlan, category: WriteCategory) -> tuple[Path, ...]:
    """Return the paths in ``plan`` carrying ``category``, in commit order."""
    return tuple(
        proposal.path.path
        for proposal in plan.proposals
        if category in proposal.categories
    )
