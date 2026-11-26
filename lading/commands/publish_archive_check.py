"""Compare crate archives with the digests ``--expect-digests`` recorded.

:class:`ArchiveVerifier` is the unit that computes and compares digests and
records outcomes. Everything else it needs is injected: where Cargo wrote an
archive (:mod:`lading.commands.publish_target_dir`), what crates.io holds
(:mod:`lading.commands.publish_registry`), and who is told of each decision
(:class:`~lading.commands.publish_archive_outcomes.ArchiveObserver`). Rendering
for the publish summary lives in :mod:`lading.commands.publish_digest_run`.

The publish pipeline calls it at these points in a crate's life:

1. before packaging, to skip a version whose identical archive is already on
   crates.io and to refuse one whose archive differs: the read-only query
   :meth:`~ArchiveVerifier.fetch_published_digest` looks without side effects,
   and the command :meth:`~ArchiveVerifier.settle_published` decides, records
   and may raise (:meth:`~ArchiveVerifier.is_already_published` reads what it
   decided);
2. :meth:`~ArchiveVerifier.check_archive` after ``cargo package`` writes the
   archive, after ``cargo publish --dry-run`` packages again, and after a live
   ``cargo publish`` (a difference there cannot undo the upload but fails the
   run loudly);
3. :meth:`~ArchiveVerifier.mark_unverified` when a dry run downgrades a failed
   ``cargo package`` or ``cargo publish --dry-run`` to a warning, so no archive
   exists to compare;
4. :meth:`~ArchiveVerifier.confirm_already_published` when Cargo reports a
   version the earlier check missed.

Each crate ends with one outcome. Digests, crate names and paths are the only
things recorded; lading never reads a registry credential here.

Examples
--------
>>> PACKAGED.matches_detail
'packaged archive matches'
"""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

from lading.commands import publish_registry
from lading.commands.publish_archive_outcomes import (
    ArchiveObserver,
    ArchiveOutcome,
    CrateOutcome,
    LoggingObserver,
)
from lading.commands.publish_digests import (
    ExpectedDigests,
    crate_archive_filename,
    sha256_file,
)
from lading.commands.publish_errors import (
    ArchiveMismatchError,
    ArchiveUnavailableError,
    PublishedArchiveConflictError,
    PublishPreflightError,
    UploadedArchiveMismatchError,
)
from lading.commands.publish_target_dir import CargoTargetLocator

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceCrate


@dc.dataclass(frozen=True, slots=True)
class _Comparison:
    """An expected digest set against the digest actually found."""

    expected: str
    actual: str

    @property
    def is_match(self) -> bool:
        """Whether the two digests are the same."""
        return self.expected == self.actual

    def describe(self) -> str:
        """Render both digests the way every mismatch message presents them."""
        return f"expected sha256 {self.expected}, actual sha256 {self.actual}"


@dc.dataclass(frozen=True, slots=True)
class ArchivePhase:
    """How a check on a local archive words and classifies its failure.

    The packaged and uploaded checks differ only in this vocabulary and in the
    error class, so one method owns the comparison and each phase supplies it.
    """

    error_cls: type[PublishPreflightError]
    differs_message: str
    matches_detail: str
    differs_detail: str


PACKAGED = ArchivePhase(
    error_cls=ArchiveMismatchError,
    differs_message=(
        "The archive for crate {crate} differs from the digest expected in "
        "{source}; refusing to publish it"
    ),
    matches_detail="packaged archive matches",
    differs_detail="packaged archive differs",
)
DRY_RUN_PUBLISH = ArchivePhase(
    error_cls=ArchiveMismatchError,
    differs_message=(
        "The archive cargo publish --dry-run packaged for crate {crate} differs "
        "from the digest expected in {source}"
    ),
    matches_detail="dry-run publish archive matches",
    differs_detail="dry-run publish archive differs",
)
UPLOADED = ArchivePhase(
    error_cls=UploadedArchiveMismatchError,
    differs_message=(
        "The uploaded bytes for crate {crate} differ from the digest expected "
        "in {source}, and the upload cannot be undone"
    ),
    matches_detail="uploaded archive matches",
    differs_detail="uploaded archive differs",
)


@dc.dataclass(slots=True)
class ArchiveVerifier:
    """Compare crate archives with the caller's expected digests.

    One instance serves one publish run and remembers each crate's outcome.

    Parameters
    ----------
    expected : ExpectedDigests
        The validated digests, covering exactly the planned crates.
    locate_target : Callable[[Path], Path]
        Returns the Cargo target directory for a crate root.
    fetch : PublishedDigestFetcher
        Reports the digest of an already-published archive.
    observer : ArchiveObserver
        Told of every outcome as it is decided.
    """

    expected: ExpectedDigests
    locate_target: cabc.Callable[[Path], Path]
    fetch: publish_registry.PublishedDigestFetcher
    observer: ArchiveObserver
    _outcomes: dict[str, CrateOutcome] = dc.field(default_factory=dict, init=False)

    def _record(
        self, crate: WorkspaceCrate, outcome: ArchiveOutcome, detail: str
    ) -> None:
        """Remember ``outcome`` for ``crate`` and tell the observer."""
        decided = CrateOutcome(crate.name, crate.version, outcome, detail)
        self._outcomes[crate.name] = decided
        self.observer.outcome_decided(decided)

    def _digest_archive(self, crate: WorkspaceCrate, crate_root: Path) -> str:
        """Hash the archive Cargo wrote for ``crate``."""
        path = (
            self.locate_target(crate_root) / "package" / crate_archive_filename(crate)
        )
        try:
            return sha256_file(path)
        except OSError as exc:
            message = (
                f"Cannot read the archive for crate {crate.name} {crate.version} "
                f"at {path}: {exc}"
            )
            raise ArchiveUnavailableError(message) from exc

    def check_archive(
        self, crate: WorkspaceCrate, crate_root: Path, phase: ArchivePhase
    ) -> None:
        """Require the archive Cargo wrote for ``crate`` to match its digest.

        ``phase`` says whether it is the archive ``cargo package`` just wrote
        (:data:`PACKAGED`, refused before anything is uploaded), the one
        ``cargo publish --dry-run`` packaged again (:data:`DRY_RUN_PUBLISH`),
        or the one ``cargo publish`` uploaded (:data:`UPLOADED`, which cannot
        be undone so the run fails loudly).

        Raises
        ------
        ArchiveMismatchError
            For :data:`PACKAGED` or :data:`DRY_RUN_PUBLISH` when the digest
            differs.
        UploadedArchiveMismatchError
            For :data:`UPLOADED` when the digest differs.
        ArchiveUnavailableError
            If the archive cannot be located or read.
        """  # ruff: ignore[docstring-extraneous-exception]  # raised through phase.error_cls and _digest_archive
        comparison = _Comparison(
            self.expected.digest_for(crate), self._digest_archive(crate, crate_root)
        )
        if not comparison.is_match:
            self._record(crate, ArchiveOutcome.MISMATCH, phase.differs_detail)
            summary = phase.differs_message.format(
                crate=f"{crate.name} {crate.version}", source=self.expected.source
            )
            message = f"{summary} ({comparison.describe()})"
            raise phase.error_cls(message)
        if self.outcome_of(crate) is ArchiveOutcome.UNVERIFIED:
            # A dry run that could not package the crate keeps saying so: a
            # later archive matching does not undo that it went unchecked
            # where the failure was downgraded.
            return
        self._record(crate, ArchiveOutcome.VERIFIED, phase.matches_detail)

    def mark_unverified(self, crate: WorkspaceCrate, reason: str) -> None:
        """Record that no archive for ``crate`` could be compared."""
        self._record(crate, ArchiveOutcome.UNVERIFIED, reason)

    def fetch_published_digest(self, crate: WorkspaceCrate) -> str | None:
        """Return the digest crates.io holds for ``crate``'s version (a query).

        Pure with respect to the verifier: it records nothing and tells nobody,
        so it can be called to look without deciding anything.

        Returns
        -------
        str | None
            The published archive's digest, or ``None`` when not yet published.
        """
        return self.fetch(crate.name, crate.version)

    def settle_published(self, crate: WorkspaceCrate) -> bool:
        """Decide, record and report an already-published version (a command).

        Fetches the published digest, compares it with the expectation, records
        the outcome and tells the observer.

        Returns
        -------
        bool
            ``True`` when crates.io holds the identical archive, so the caller
            skips the crate; ``False`` when the version is not yet published.

        Raises
        ------
        PublishedArchiveConflictError
            If the published archive differs from the expectation.
        """
        published = self.fetch_published_digest(crate)
        if published is None:
            return False
        comparison = _Comparison(self.expected.digest_for(crate), published)
        if not comparison.is_match:
            self._record(crate, ArchiveOutcome.CONFLICT, "published archive differs")
            message = (
                f"Crate {crate.name} {crate.version} is already published with a "
                f"different archive ({comparison.describe()}). A "
                "published version cannot be replaced; release a new version"
            )
            raise PublishedArchiveConflictError(message)
        if self.outcome_of(crate) is not ArchiveOutcome.UNVERIFIED:
            # An unverified crate keeps saying so: nothing was compared locally,
            # and the published bytes are not the archive this run packaged.
            self._record(
                crate, ArchiveOutcome.ALREADY_PUBLISHED, "skipping cargo publish"
            )
        return True

    def outcome_of(self, crate: WorkspaceCrate) -> ArchiveOutcome | None:
        """Return the outcome decided so far for ``crate``, if any."""
        decided = self._outcomes.get(crate.name)
        return None if decided is None else decided.outcome

    def is_already_published(self, crate: WorkspaceCrate) -> bool:
        """Whether ``settle_published`` found the identical archive on crates.io."""
        return self.outcome_of(crate) is ArchiveOutcome.ALREADY_PUBLISHED

    def confirm_already_published(self, crate: WorkspaceCrate) -> None:
        """Compare a version Cargo says exists, when the pre-publish check missed it.

        Raises
        ------
        ArchiveUnavailableError
            If crates.io does not serve the archive Cargo says exists, so it
            cannot be compared.
        """
        if not self.settle_published(crate):
            message = (
                f"cargo reports that crate {crate.name} {crate.version} is "
                "already published, but crates.io does not serve its archive, so "
                "it cannot be compared with the expected digest"
            )
            raise ArchiveUnavailableError(message)

    @property
    def outcomes(self) -> cabc.Sequence[CrateOutcome]:
        """Each decided crate's outcome, in the order they were decided."""
        return tuple(self._outcomes.values())


def create_verifier(
    expected: ExpectedDigests,
    runner: CommandRunner,
    fetch: publish_registry.PublishedDigestFetcher | None = None,
) -> ArchiveVerifier:
    """Build the production verifier: Cargo-located archives, logged outcomes.

    Parameters
    ----------
    expected : ExpectedDigests
        The validated digests.
    runner : CommandRunner
        Runs ``cargo metadata`` to locate archives.
    fetch : PublishedDigestFetcher | None
        Replacement for the crates.io download; tests inject a fake.

    Returns
    -------
    ArchiveVerifier
        A verifier wired to a :class:`CargoTargetLocator` and a
        :class:`LoggingObserver`.
    """
    return ArchiveVerifier(
        expected,
        CargoTargetLocator(runner),
        publish_registry.fetch_crates_io_digest if fetch is None else fetch,
        LoggingObserver(),
    )
