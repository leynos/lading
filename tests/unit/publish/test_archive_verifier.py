"""Unit tests for :class:`~lading.commands.publish_archive_check.ArchiveVerifier`."""

import collections.abc as cabc
import typing as typ

import pytest

from lading.commands.publish_archive_check import (
    PACKAGED,
    UPLOADED,
    ArchiveOutcome,
    ArchiveUnavailableError,
    ArchiveVerifier,
)
from lading.commands.publish_digests import ExpectedDigests
from lading.commands.publish_errors import ArchiveMismatchError
from tests.helpers.publish_digests import PLAIN_ARCHIVE, FakeCargo, digest

from .conftest import make_crate

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.runtime import CommandRunner


def _verifier(runner: CommandRunner) -> ArchiveVerifier:
    """Return a verifier expecting ``PLAIN_ARCHIVE`` for crate ``alpha``."""
    expected = ExpectedDigests("d", {"alpha-0.1.0.crate": digest(PLAIN_ARCHIVE)})
    return ArchiveVerifier(expected, runner)


def test_missing_archive_is_reported_with_its_path(tmp_path: Path) -> None:
    """``cargo package`` that wrote nothing cannot be treated as a match."""
    crate = make_crate(tmp_path, "alpha")
    verifier = _verifier(FakeCargo())

    with pytest.raises(ArchiveUnavailableError) as excinfo:
        verifier.check_archive(crate, crate.root_path, PACKAGED)

    assert "alpha-0.1.0.crate" in str(excinfo.value), "the path must be named"
    assert not verifier.outcomes, "no outcome is recorded without a comparison"


@pytest.mark.parametrize(
    "stdout",
    ["not json", "{}", "[]", '{"target_directory": 3}'],
    ids=["not_json", "no_key", "not_object", "not_a_path"],
)
def test_unusable_cargo_metadata_is_reported(tmp_path: Path, stdout: str) -> None:
    """A target directory that cannot be read stops the run with a reason."""
    crate = make_crate(tmp_path, "alpha")

    def runner(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        """Return output ``cargo metadata`` could not have produced."""
        return 101, stdout, "error: could not read the manifest"

    with pytest.raises(ArchiveUnavailableError, match="target directory"):
        _verifier(runner).check_archive(crate, crate.root_path, PACKAGED)


def test_target_directory_is_asked_for_once(tmp_path: Path) -> None:
    """Both the packaged and the uploaded check share one ``cargo metadata``."""
    crate = make_crate(tmp_path, "alpha")
    cargo = FakeCargo(packaged={"alpha": PLAIN_ARCHIVE})
    cargo(("cargo", "package"), cwd=crate.root_path)
    verifier = _verifier(cargo)

    verifier.check_archive(crate, crate.root_path, PACKAGED)
    verifier.check_archive(crate, crate.root_path, UPLOADED)

    assert len(cargo.subcommands("metadata")) == 1, (
        "cargo metadata must run once and be remembered"
    )


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
    assert verifier.summary_lines()[0] == "Archive checks against d:", (
        "the summary must open by naming the digest source"
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
