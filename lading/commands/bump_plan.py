"""Immutable bump write plan and the proposal builder that produces it.

A bump run computes every intended write before it changes anything. Each
proposal is keyed by its filesystem identity, so two stages that target the
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
    BumpPreflightError,
    BumpTargetExaminationError,
    ConfinedPath,
)

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
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

type _ProposalKey = tuple[str, int, int] | tuple[str, int, int, str] | tuple[str, str]


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
    _contents: dict[_ProposalKey, str] = dc.field(default_factory=dict)
    _categories: dict[_ProposalKey, set[WriteCategory]] = dc.field(default_factory=dict)
    _paths: dict[_ProposalKey, ConfinedPath] = dc.field(default_factory=dict)
    _original: dict[_ProposalKey, str | None] = dc.field(default_factory=dict)
    _order: list[_ProposalKey] = dc.field(default_factory=list)
    _casefold_keys: dict[_ProposalKey, _ProposalKey] = dc.field(default_factory=dict)

    def _key(self, path: ConfinedPath) -> _ProposalKey:
        """Return a filesystem-aware identity for ``path``.

        An existing target is identified by its device and inode, so two
        spellings that name the same file compose even on a case-insensitive
        filesystem whose path comparison is case-preserving, such as macOS
        APFS. An absent target has no inode yet, so it is identified by its
        parent's device and inode plus its exact name. Distinct parents stay
        distinct. Without a filesystem identity, two spellings that differ only
        in case cannot be told apart: they may name one future file on a
        case-insensitive filesystem or two files on a case-sensitive one, so
        the plan rejects the ambiguity instead of guessing.

        Returns
        -------
        _ProposalKey
            The identity used as the proposal key.
        """
        try:
            status = path.path.stat()
        except FileNotFoundError:
            return self._absent_key(path)
        except OSError:
            return ("path", str(path.path))
        if status.st_ino == 0:
            # A platform may report no inode for distinct files on one device.
            # The exact spelling keeps case-only targets distinct even on a
            # case-sensitive Windows directory, where normcase would merge them.
            return ("path", str(path.path))
        return ("file", status.st_dev, status.st_ino)

    def _absent_key(self, path: ConfinedPath) -> _ProposalKey:
        """Return the exact-name identity of a target that does not exist yet.

        A second spelling that differs only in case is rejected by
        :meth:`_require_unambiguous_case` before any write.

        Returns
        -------
        _ProposalKey
            The parent identity plus the exact target name.
        """
        try:
            parent = path.path.parent.stat()
        except OSError:
            return ("path", str(path.path))
        if parent.st_ino == 0:
            return ("path", str(path.path))
        return ("absent", parent.st_dev, parent.st_ino, path.path.name)

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
        key = self._key(path)
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
        key = self._key(path)
        self._require_unambiguous_case(key, path)
        if key in self._contents:
            if content == self._contents[key]:
                # A later stage adopted the pending text unchanged, so the
                # plan must not claim another category for it.
                return
            original = self._original.get(key)
            if content == original:
                # A later stage restored the file's pre-bump content, so the
                # plan must not replace it or report a change.
                del self._contents[key]
                del self._categories[key]
                del self._paths[key]
                self._original.pop(key, None)
                self._order.remove(key)
                self._casefold_keys.pop(self._casefold_key(key), None)
                return
            self._contents[key] = content
            self._categories[key].add(category)
            return
        current = self.read(path)
        self._original[key] = current
        if current == content:
            return
        self._contents[key] = content
        self._categories[key] = {category}
        self._paths[key] = path
        self._order.append(key)

    def _casefold_key(self, key: _ProposalKey) -> _ProposalKey:
        """Return the case-folded identity used to detect ambiguous targets.

        Existing targets have a real inode identity and are left unchanged. An
        absent or identity-free target may name the same file as another
        spelling on a case-insensitive filesystem, so its spelling is folded
        only for the ambiguity check.

        Returns
        -------
        _ProposalKey
            The case-folded identity.
        """
        match key:
            case ("absent", device, inode, name):
                return ("absent", device, inode, name.casefold())
            case ("path", spelling):
                return ("path", spelling.casefold())
            case _:
                return key

    def _require_unambiguous_case(
        self,
        key: _ProposalKey,
        path: ConfinedPath,
    ) -> None:
        """Reject two planned targets that differ only in case.

        Without a filesystem identity, the plan cannot tell whether the
        spellings name one future file or two distinct files. Writing both
        could overwrite the first target, so the ambiguity is rejected before
        any mutation.

        Raises
        ------
        BumpPreflightError
            If a different spelling of the same case-folded target is already
            planned.
        """
        folded = self._casefold_key(key)
        existing = self._casefold_keys.get(folded)
        if existing is not None and existing != key:
            message = (
                "two planned targets differ only in case and cannot be "
                f"identified as one file: {self._paths[existing].display} and "
                f"{path.display}"
            )
            raise BumpPreflightError(message)
        self._casefold_keys[folded] = key

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
                path=self._paths[key],
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

    def _rank(self, key: _ProposalKey) -> tuple[int, str]:
        """Return the commit rank for one proposed path."""
        categories = self._categories[key]
        path = self._paths[key]
        display = PurePosixPath(*path.parts).as_posix()
        if WriteCategory.MANIFEST in categories:
            # The workspace manifest is written before member manifests.
            return (0 if path.parts == _ROOT_MANIFEST_PARTS else 1, display)
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
    workflow provides. The guarantee applies to the paths as validated: a
    concurrent process that replaces a validated parent with a symlink after
    validation is not defended against.
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
        transposed_readmes=tuple(sorted(_paths_for(plan, WriteCategory.README))),
        lockfiles=_root_first(lockfiles),
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
