"""Unit tests for :class:`~lading.commands.publish_archive_check.ArchiveVerifier`."""

import logging
import typing as typ

import pytest

from lading.commands.publish_archive_check import (
    PACKAGED,
    ArchiveVerifier,
    create_verifier,
)
from lading.commands.publish_archive_outcomes import ArchiveOutcome, CrateOutcome
from lading.commands.publish_digests import ExpectedDigests
from lading.commands.publish_errors import (
    ArchiveMismatchError,
    ArchiveUnavailableError,
)
from lading.utils import metrics
from tests.helpers.publish_digests import (
    OTHER_ARCHIVE,
    PLAIN_ARCHIVE,
    FakeCargo,
    digest,
)

from .conftest import make_crate

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from lading.runtime import CommandRunner


def _verifier(runner: CommandRunner) -> ArchiveVerifier:
    """Return a verifier expecting ``PLAIN_ARCHIVE`` for crate ``alpha``."""
    expected = ExpectedDigests("d", {"alpha-0.1.0.crate": digest(PLAIN_ARCHIVE)})
    return create_verifier(expected, runner)


def test_missing_archive_is_reported_with_its_path(tmp_path: Path) -> None:
    """``cargo package`` that wrote nothing cannot be treated as a match."""
    crate = make_crate(tmp_path, "alpha")
    verifier = _verifier(FakeCargo())

    with pytest.raises(ArchiveUnavailableError) as excinfo:
        verifier.check_archive(crate, crate.root_path, PACKAGED)

    assert "alpha-0.1.0.crate" in str(excinfo.value), "the path must be named"
    assert not verifier.outcomes, "no outcome is recorded without a comparison"


def test_outcomes_are_kept_in_decision_order(tmp_path: Path) -> None:
    """The summary lists crates in the order their outcomes were decided."""
    first = make_crate(tmp_path, "alpha")
    cargo = FakeCargo(packaged={"alpha": PLAIN_ARCHIVE})
    cargo(("cargo", "package"), cwd=first.root_path)
    verifier = _verifier(cargo)
    second = make_crate(tmp_path, "beta")

    verifier.mark_unverified(second, "no archive")
    verifier.check_archive(first, first.root_path, PACKAGED)

    assert [outcome.crate for outcome in verifier.outcomes] == ["beta", "alpha"], (
        "outcomes must keep the order they were decided in"
    )
    assert verifier.outcomes[0].outcome is ArchiveOutcome.UNVERIFIED, (
        "the unmatched crate must be recorded as unverified"
    )


def test_mismatch_leaves_a_mismatch_outcome(tmp_path: Path) -> None:
    """A refused archive is recorded as a mismatch before the error escapes."""
    crate = make_crate(tmp_path, "alpha")
    cargo = FakeCargo(packaged={"alpha": b"different"})
    cargo(("cargo", "package"), cwd=crate.root_path)
    verifier = _verifier(cargo)

    with pytest.raises(ArchiveMismatchError):
        verifier.check_archive(crate, crate.root_path, PACKAGED)

    assert verifier.outcomes[0].outcome is ArchiveOutcome.MISMATCH, (
        "a refused archive must be recorded as a mismatch"
    )


def test_target_lookup_reads_cargo_metadata_without_echoing_it(
    tmp_path: Path,
) -> None:
    """The metadata JSON is captured for the target directory, never relayed."""
    crate = make_crate(tmp_path, "alpha")
    cargo = FakeCargo(packaged={"alpha": PLAIN_ARCHIVE})
    cargo(("cargo", "package"), cwd=crate.root_path)
    echoes: list[tuple[tuple[str, ...], bool]] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record whether each command would be echoed, then run the fake."""
        echoes.append((tuple(command), echo_stdout))
        return cargo(command, cwd=cwd, env=env, echo_stdout=echo_stdout)

    _verifier(runner).check_archive(crate, crate.root_path, PACKAGED)

    metadata = [echo for command, echo in echoes if command[1] == "metadata"]
    assert metadata == [False], "cargo metadata output must not be echoed"


class _RecordingObserver:
    """An observer double that remembers what it was told."""

    def __init__(self) -> None:
        """Start with nothing observed."""
        self.seen: list[CrateOutcome] = []
        self.announcements = 0

    def outcome_decided(self, decided: CrateOutcome) -> None:
        """Remember ``decided`` and count the announcement."""
        self.announcements += 1
        self.seen.append(decided)


def _injected(
    tmp_path: Path, published: str | None
) -> tuple[ArchiveVerifier, _RecordingObserver]:
    """Build a verifier from injected parts only: no Cargo, no network, no logs."""
    observer = _RecordingObserver()
    expected = ExpectedDigests("d", {"alpha-0.1.0.crate": digest(PLAIN_ARCHIVE)})
    verifier = ArchiveVerifier(
        expected,
        lambda root: root / "target",
        lambda name, version: published,
        observer,
    )
    return verifier, observer


def test_fetching_a_published_digest_is_a_pure_query(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The query records nothing, notifies nobody, counts nothing, and logs nothing."""
    caplog.set_level(logging.DEBUG)
    metrics.reset()
    crate = make_crate(tmp_path, "alpha")
    verifier, observer = _injected(tmp_path, digest(OTHER_ARCHIVE))

    result = verifier.fetch_published_digest(crate)

    assert result == digest(OTHER_ARCHIVE), "the query must return the registry digest"
    assert not verifier.outcomes, "no outcome may be recorded by a query"
    assert not observer.seen, "the observer must not be told"
    assert not caplog.records, "nothing may be logged"
    assert not metrics.snapshot(), "nothing may be counted"


def test_settling_a_published_version_is_an_explicit_command(tmp_path: Path) -> None:
    """The command records the decision and tells the observer."""
    crate = make_crate(tmp_path, "alpha")
    verifier, observer = _injected(tmp_path, digest(PLAIN_ARCHIVE))

    skipped = verifier.settle_published(crate)

    assert skipped is True, "an identical archive means the crate is skipped"
    assert verifier.is_already_published(crate), "the outcome must be recorded"
    assert [item.outcome for item in observer.seen] == [
        ArchiveOutcome.ALREADY_PUBLISHED
    ], "the observer must be told exactly once"


def test_settling_an_unpublished_version_decides_nothing(tmp_path: Path) -> None:
    """A version crates.io does not hold is neither recorded nor announced."""
    crate = make_crate(tmp_path, "alpha")
    verifier, observer = _injected(tmp_path, None)

    assert verifier.settle_published(crate) is False, "not published means proceed"
    assert not verifier.outcomes, "nothing may be recorded"
    assert not observer.seen, "nothing may be announced"


def test_verifier_compares_archives_through_an_injected_locator(
    tmp_path: Path,
) -> None:
    """The outcome logic needs no Cargo: the locator is a plain function."""
    crate = make_crate(tmp_path, "alpha")
    verifier, observer = _injected(tmp_path, None)
    package_dir = crate.root_path / "target" / "package"
    package_dir.mkdir(parents=True)
    (package_dir / "alpha-0.1.0.crate").write_bytes(PLAIN_ARCHIVE)

    verifier.check_archive(crate, crate.root_path, PACKAGED)

    assert [item.outcome for item in observer.seen] == [ArchiveOutcome.VERIFIED], (
        "a matching archive found through the injected locator is verified"
    )
