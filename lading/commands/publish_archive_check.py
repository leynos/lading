"""Check packaged and uploaded archives against ``--expect-digests``.

:class:`ArchiveVerifier` is the side-effecting half of the feature whose pure
half lives in :mod:`lading.commands.publish_digests`. The publish pipeline
calls it at four points in a crate's life:

1. :meth:`~ArchiveVerifier.check_packaged` after ``cargo package`` writes the
   archive, before anything is uploaded;
2. :meth:`~ArchiveVerifier.mark_unverified` when a dry run downgrades a failed
   ``cargo package`` to a warning, so no archive exists to compare;
3. :meth:`~ArchiveVerifier.resolve_published` before ``cargo publish``, to skip
   a version whose identical archive is already on crates.io and to refuse one
   whose archive differs;
4. :meth:`~ArchiveVerifier.check_uploaded` after a successful live publish,
   which cannot undo the upload but makes the run fail loudly.

Each crate ends with one :class:`ArchiveOutcome`, logged when it is decided and
rendered by :meth:`~ArchiveVerifier.summary_lines` for the publish summary.
Digests, crate names and paths are the only things logged; lading never reads
a registry credential here.

Examples
--------
>>> ArchiveOutcome.VERIFIED.value
'verified'
"""

import collections.abc as cabc
import dataclasses as dc
import enum
import json
import logging
import typing as typ
from pathlib import Path

from lading.commands import publish_registry
from lading.commands.publish_digests import (
    ExpectedDigests,
    crate_archive_filename,
    sha256_file,
)
from lading.commands.publish_errors import (
    ArchiveMismatchError,
    PublishedArchiveConflictError,
    PublishError,
    PublishPreflightError,
    UploadedArchiveMismatchError,
)
from lading.utils import metrics

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceCrate

LOGGER = logging.getLogger(__name__)

#: Counter incremented once per crate outcome, labelled by ``outcome``.
ARCHIVE_CHECK_METRIC = "publish.archive_check"

_METADATA_COMMAND = ("cargo", "metadata", "--format-version", "1", "--no-deps")


class ArchiveOutcome(enum.StrEnum):
    """What lading established about one crate's archive."""

    VERIFIED = "verified"
    ALREADY_PUBLISHED = "already published and identical"
    CONFLICT = "conflict"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"


@dc.dataclass(frozen=True, slots=True)
class CrateOutcome:
    """The outcome for one crate, with the reason it was reached.

    Attributes
    ----------
    crate : str
        The crate's name.
    version : str
        The crate's version.
    outcome : ArchiveOutcome
        What was established.
    detail : str
        A short human-readable reason, shown in the publish summary.
    """

    crate: str
    version: str
    outcome: ArchiveOutcome
    detail: str

    def summary_line(self) -> str:
        """Return the line this outcome occupies in the publish summary."""
        return f"- {self.crate} {self.version}: {self.outcome.value} ({self.detail})"


_FAILURES = frozenset({ArchiveOutcome.CONFLICT, ArchiveOutcome.MISMATCH})


class ArchiveUnavailableError(PublishError):
    """Raise when an archive that must be compared cannot be located or read."""


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
class _ArchivePhase:
    """How a check on a local archive words and classifies its failure.

    The packaged and uploaded checks differ only in this vocabulary and in the
    error class, so one method owns the comparison and each phase supplies it.
    """

    error_cls: type[PublishPreflightError]
    differs_message: str
    matches_detail: str
    differs_detail: str


_PACKAGED = _ArchivePhase(
    error_cls=ArchiveMismatchError,
    differs_message=(
        "The archive for crate {crate} differs from the digest expected in "
        "{source}; refusing to publish it"
    ),
    matches_detail="packaged archive matches",
    differs_detail="packaged archive differs",
)
_UPLOADED = _ArchivePhase(
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

    One instance serves one publish run. It remembers each crate's outcome so
    the summary can report them, and the workspace's Cargo target directory so
    ``cargo metadata`` is asked once.

    Parameters
    ----------
    expected : ExpectedDigests
        The validated digests, covering exactly the planned crates.
    runner : CommandRunner
        Runs ``cargo metadata`` in the staged workspace to find where Cargo
        writes archives; it honours ``CARGO_TARGET_DIR`` and Cargo
        configuration, so lading never assumes ``target/``.
    fetch : PublishedDigestFetcher
        Reports the digest of an already-published archive. Defaults to the
        crates.io downloader; tests inject a fake.
    """

    expected: ExpectedDigests
    runner: CommandRunner
    fetch: publish_registry.PublishedDigestFetcher = (
        publish_registry.fetch_crates_io_digest
    )
    _outcomes: dict[str, CrateOutcome] = dc.field(default_factory=dict, init=False)
    _target_directory: Path | None = dc.field(default=None, init=False)

    def _record(
        self, crate: WorkspaceCrate, outcome: ArchiveOutcome, detail: str
    ) -> None:
        """Store, count, and log ``outcome`` for ``crate``."""
        self._outcomes[crate.name] = CrateOutcome(
            crate.name, crate.version, outcome, detail
        )
        metrics.increment_counter(ARCHIVE_CHECK_METRIC, outcome=outcome.name.lower())
        log = LOGGER.error if outcome in _FAILURES else LOGGER.info
        log(
            "Archive check for crate %s %s: %s (%s)",
            crate.name,
            crate.version,
            outcome.value,
            detail,
        )

    def _target(self, crate_root: Path) -> Path:
        """Return the workspace's Cargo target directory, asking Cargo once."""
        if self._target_directory is None:
            self._target_directory = self._ask_cargo_for_target(crate_root)
        return self._target_directory

    def _ask_cargo_for_target(self, crate_root: Path) -> Path:
        """Run ``cargo metadata`` in ``crate_root`` and read ``target_directory``."""
        exit_code, stdout, stderr = self.runner(
            _METADATA_COMMAND, cwd=crate_root, env=None
        )
        try:
            return Path(json.loads(stdout)["target_directory"])
        except (ValueError, KeyError, TypeError) as exc:
            message = (
                "Cannot find the Cargo target directory to locate the packaged "
                f"archive (cargo metadata exited {exit_code}): "
                f"{(stderr or stdout).strip()[:200]}"
            )
            raise ArchiveUnavailableError(message) from exc

    def _archive_path(self, crate: WorkspaceCrate, crate_root: Path) -> Path:
        """Return where Cargo wrote ``crate``'s archive."""
        return self._target(crate_root) / "package" / crate_archive_filename(crate)

    def _digest_archive(self, crate: WorkspaceCrate, crate_root: Path) -> str:
        """Hash the archive Cargo wrote for ``crate``."""
        path = self._archive_path(crate, crate_root)
        try:
            return sha256_file(path)
        except OSError as exc:
            message = (
                f"Cannot read the archive for crate {crate.name} {crate.version} "
                f"at {path}: {exc}"
            )
            raise ArchiveUnavailableError(message) from exc

    def _require_match(
        self, crate: WorkspaceCrate, crate_root: Path, phase: _ArchivePhase
    ) -> None:
        """Hash ``crate``'s archive and fail with ``phase``'s error if it differs."""
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
        self._record(crate, ArchiveOutcome.VERIFIED, phase.matches_detail)

    def check_packaged(self, crate: WorkspaceCrate, crate_root: Path) -> None:
        """Require the archive ``cargo package`` just wrote to match.

        Raises
        ------
        ArchiveMismatchError
            If the digest differs; nothing has been uploaded for this crate or
            any later one.
        ArchiveUnavailableError
            If the archive cannot be located or read.
        """  # ruff: ignore[docstring-extraneous-exception]  # raised via _require_match
        self._require_match(crate, crate_root, _PACKAGED)

    def mark_unverified(self, crate: WorkspaceCrate, reason: str) -> None:
        """Record that no archive for ``crate`` could be compared."""
        self._record(crate, ArchiveOutcome.UNVERIFIED, reason)

    def resolve_published(self, crate: WorkspaceCrate) -> bool:
        """Compare an already-published version with the expectation.

        Returns
        -------
        bool
            ``True`` when crates.io holds the identical archive, so the caller
            skips ``cargo publish``; ``False`` when the version is not yet
            published.

        Raises
        ------
        PublishedArchiveConflictError
            If the published archive differs from the expectation.
        """
        published = self.fetch(crate.name, crate.version)
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
        self._record(
            crate,
            ArchiveOutcome.ALREADY_PUBLISHED,
            "skipping cargo publish",
        )
        return True

    def confirm_already_published(self, crate: WorkspaceCrate) -> None:
        """Compare a version Cargo says exists, when the pre-publish check missed it.

        Raises
        ------
        ArchiveUnavailableError
            If crates.io does not serve the archive Cargo says exists, so it
            cannot be compared.
        """
        if not self.resolve_published(crate):
            message = (
                f"cargo reports that crate {crate.name} {crate.version} is "
                "already published, but crates.io does not serve its archive, so "
                "it cannot be compared with the expected digest"
            )
            raise ArchiveUnavailableError(message)

    def check_uploaded(self, crate: WorkspaceCrate, crate_root: Path) -> None:
        """Require the archive ``cargo publish`` uploaded to match.

        Raises
        ------
        UploadedArchiveMismatchError
            If the digest differs. The upload cannot be undone, so this makes
            the run fail loudly rather than repair anything.
        ArchiveUnavailableError
            If the archive cannot be located or read.
        """  # ruff: ignore[docstring-extraneous-exception]  # raised via _require_match
        self._require_match(crate, crate_root, _UPLOADED)

    @property
    def outcomes(self) -> cabc.Sequence[CrateOutcome]:
        """Each decided crate's outcome, in the order they were decided."""
        return tuple(self._outcomes.values())

    def summary_lines(self) -> tuple[str, ...]:
        """Return the lines the publish summary shows for archive checks."""
        header = f"Archive checks against {self.expected.source}:"
        return (header, *(outcome.summary_line() for outcome in self.outcomes))
