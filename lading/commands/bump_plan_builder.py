"""Proposal builder for the bump write plan.

:class:`PlanBuilder` accumulates every intended write before anything is
committed. Each proposal is keyed by its filesystem identity, so two stages
that target the same file compose into one write while retaining both change
categories. Later stages read earlier proposed content through
:meth:`PlanBuilder.read`, which keeps, for example, an adopted README consistent
with a documentation rewrite of the workspace README.

The frozen plan and its commit step live in
:mod:`lading.commands.bump_plan`.
"""

import dataclasses as dc
import typing as typ
from pathlib import Path, PurePosixPath

from lading.commands.bump_plan import BumpPlan, PlannedWrite, WriteCategory
from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    ConfinedPath,
)
from lading.utils.text import normalize_newlines

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.commands.bump_lockfile_planning import LockfilePlan
    from lading.commands.bump_preview import DocumentationPreview

__all__ = ["PlanBuilder"]

_ROOT_MANIFEST_PARTS: typ.Final[tuple[str, ...]] = ("Cargo.toml",)


type _ProposalKey = tuple[str, int, int] | tuple[str, int, int, str] | tuple[str, str]


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
    _document_previews: dict[_ProposalKey, DocumentationPreview] = dc.field(
        default_factory=dict
    )
    _casefold_keys: dict[_ProposalKey, _ProposalKey] = dc.field(default_factory=dict)
    _casefold_paths: dict[_ProposalKey, str] = dc.field(default_factory=dict)

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

    def read(self, path: ConfinedPath, *, raw: bool = False) -> str | None:
        """Return the current content of ``path`` honouring earlier proposals.

        Parameters
        ----------
        path : ConfinedPath
            A confined workspace path.
        raw : bool
            Keep CRLF and lone-CR sequences exactly as they are on disk. The
            documentation diff needs that; a rewrite wants normal lines.

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
            return path.path.read_text(encoding="utf-8", newline="" if raw else None)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            message = f"cannot read bump target {path.display}: {exc}"
            raise BumpTargetExaminationError(message) from exc

    def read_raw(self, path: ConfinedPath) -> str | None:
        """Return :meth:`read` output with raw newline sequences preserved.

        Returns
        -------
        str | None
            The proposed or raw on-disk text, or ``None`` when absent.
        """
        return self.read(path, raw=True)

    def propose(
        self,
        path: ConfinedPath,
        content: str,
        category: WriteCategory,
        *,
        observed: str | None = None,
    ) -> bool:
        """Record ``content`` for ``path`` unless it is already current.

        Parameters
        ----------
        path : ConfinedPath
            A confined workspace path.
        content : str
            The complete text the stage wants the file to contain.
        category : WriteCategory
            The stage's role for reporting purposes.
        observed : str | None
            The text the caller already read for ``path``. Supplying it keeps
            the proposal on that snapshot instead of reading the file again,
            which could observe a different file and leave a preview that does
            not match the plan.

        Returns
        -------
        bool
            ``True`` when the plan holds a write for ``path`` after this call.
        """
        key = self._key(path)
        self._require_unambiguous_case(key, path)
        if key in self._contents:
            if content == self._contents[key]:
                # A later stage adopted the pending text unchanged, so the
                # plan must not claim another category for it.
                return True
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
                self._casefold_paths.pop(self._casefold_key(key), None)
                # A preview exists only while its write does: a later stage
                # that restores the pre-bump content must not leave a diff for
                # a file the plan no longer writes. Removal uses the write's
                # filesystem identity, not a path spelling.
                self._document_previews.pop(key, None)
                return False
            self._contents[key] = content
            self._categories[key].add(category)
            self._update_preview(key, content)
            return True
        current = self.read(path) if observed is None else normalize_newlines(observed)
        self._original[key] = current
        if current == content:
            return False
        self._contents[key] = content
        self._categories[key] = {category}
        self._paths[key] = path
        self._order.append(key)
        return True

    def _update_preview(self, key: _ProposalKey, content: str) -> None:
        """Keep a recorded preview describing the write the plan performs."""
        preview = self._document_previews.get(key)
        if preview is not None:
            self._document_previews[key] = dc.replace(preview, proposed=content)

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
                f"identified as one file: {self._casefold_paths[folded]} and "
                f"{path.display}"
            )
            raise BumpPreflightError(message)
        self._casefold_keys[folded] = key
        self._casefold_paths[folded] = path.display

    def record_documentation_preview(
        self,
        path: ConfinedPath,
        preview: DocumentationPreview,
    ) -> None:
        """Record the documentation stage's exact original and proposed text.

        Parameters
        ----------
        path : ConfinedPath
            The confined target whose write the preview describes. The preview
            is keyed by this path's filesystem identity, so removal matches the
            write even when another stage spells the path differently.
        preview : DocumentationPreview
            The text the documentation stage read and the text it proposes.
        """
        self._document_previews[self._key(path)] = preview

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
            document_previews=tuple(self._document_previews.values()),
        )

    def _rank(self, key: _ProposalKey) -> tuple[int, str]:
        """Return the commit rank for one proposed path."""
        categories = self._categories[key]
        path = self._paths[key]
        display = PurePosixPath(*path.parts).as_posix()
        if WriteCategory.MANIFEST in categories:
            # The workspace manifest is written before member manifests.
            return (0 if path.parts == _ROOT_MANIFEST_PARTS else 1, display)
        if WriteCategory.FIXTURE_MANIFEST in categories:
            # Configured fixture rewrites follow member manifests and precede
            # documentation, README and lockfile work.
            return (2, display)
        if WriteCategory.DOCUMENTATION in categories:
            return (3, display)
        return (4, display)
