"""Pipeline tests for ``lading publish --expect-digests`` in live mode."""

import logging
from pathlib import Path

import pytest

from lading.commands import publish
from lading.commands.publish_archive_check import (
    ARCHIVE_CHECK_METRIC,
    ArchiveUnavailableError,
)
from lading.commands.publish_errors import (
    ArchiveMismatchError,
    ExpectedDigestsError,
    PublishedArchiveConflictError,
    UploadedArchiveMismatchError,
)
from lading.utils import metrics
from tests.helpers.publish_digests import (
    OTHER_ARCHIVE,
    PLAIN_ARCHIVE,
    PublishRig,
    digest,
)

LIVE_ORDER = ["alpha", "beta", "gamma"]
_ALREADY_EXISTS = "error: crate alpha@0.1.0 already exists on crates.io index"


def test_matching_archives_are_published_with_locked_cargo(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verified archives upload, and both cargo commands run ``--locked``."""
    summary = digest_rig.run(
        monkeypatch, live=True, expect_digests=digest_rig.digests_file()
    )

    assert digest_rig.cargo.crates_run("publish") == LIVE_ORDER, (
        "every crate is published"
    )
    for command in (
        *digest_rig.cargo.subcommands("package"),
        *digest_rig.cargo.subcommands("publish"),
    ):
        assert "--locked" in command, f"{command} must not update the lockfile"
    assert "Archive checks against" in summary, "the summary must report the checks"
    for name in LIVE_ORDER:
        assert f"- {name} 0.1.0: verified" in summary, f"{name} must be verified"


def test_archive_is_found_in_the_workspace_target_directory(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive is read from where Cargo reports its target, not ``target/``."""
    digest_rig.cargo.target_name = "elsewhere"

    digest_rig.run(
        monkeypatch, live=True, expect_digests=digest_rig.digests_file(), cleanup=False
    )

    staged = digest_rig.build_directory / "workspace"
    assert (staged / "elsewhere" / "package" / "alpha-0.1.0.crate").is_file(), (
        "the archive must be where cargo reported its target directory"
    )
    assert not (staged / "target").exists(), "nothing may assume target/"
    assert len(digest_rig.cargo.subcommands("metadata")) == len(LIVE_ORDER), (
        "Cargo is asked once per run"
    )


def test_relative_digest_file_resolves_against_the_workspace_root(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative ``--expect-digests`` path is read beneath the workspace root."""
    digest_rig.digests_file()

    digest_rig.run(monkeypatch, live=True, expect_digests=Path("digests.txt"))

    assert digest_rig.cargo.crates_run("publish") == LIVE_ORDER, (
        "every crate must be attempted in plan order"
    )


def test_mismatched_archive_stops_the_run_before_cargo_publish(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packaged archive that differs is refused, with the crate and digests."""
    digest_rig.cargo.packaged["beta"] = OTHER_ARCHIVE

    with pytest.raises(ArchiveMismatchError) as excinfo:
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    message = str(excinfo.value)
    assert "beta 0.1.0" in message, "the error names the crate"
    assert digest(PLAIN_ARCHIVE) in message, "the error shows the expected digest"
    assert digest(OTHER_ARCHIVE) in message, "the error shows the actual digest"
    assert digest_rig.cargo.crates_run("publish") == ["alpha"], (
        "beta and gamma must not be published, and alpha was already out"
    )
    assert digest_rig.cargo.crates_run("package") == ["alpha", "beta"], (
        "no crate after the mismatch is even packaged"
    )


def test_archive_that_differs_after_upload_fails_the_run(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes ``cargo publish`` left are compared once the upload succeeds."""
    digest_rig.cargo.uploaded["beta"] = OTHER_ARCHIVE

    with pytest.raises(UploadedArchiveMismatchError) as excinfo:
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    message = str(excinfo.value)
    assert "uploaded bytes for crate beta 0.1.0 differ" in message, (
        "the error must say the uploaded bytes differ"
    )
    assert "cannot be undone" in message, (
        "the error must say the upload cannot be undone"
    )
    assert digest(OTHER_ARCHIVE) in message, "the error must show the uploaded digest"
    assert digest_rig.cargo.crates_run("package") == ["alpha", "beta"], (
        "gamma never starts"
    )


def test_identical_published_archive_is_skipped(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A version already on crates.io with the expected bytes is not republished."""
    caplog.set_level(logging.INFO)
    digest_rig.registry.published["alpha"] = PLAIN_ARCHIVE

    summary = digest_rig.run(
        monkeypatch, live=True, expect_digests=digest_rig.digests_file()
    )

    assert digest_rig.cargo.crates_run("publish") == ["beta", "gamma"], (
        "alpha must be skipped without running cargo publish"
    )
    assert "- alpha 0.1.0: already published and identical" in summary, (
        "the summary must report the identical published archive"
    )
    assert any("already published and identical" in m for m in caplog.messages), (
        "the identical published archive must be logged"
    )
    assert digest_rig.registry.requests[0] == ("alpha", "0.1.0"), (
        "only name and version"
    )


def test_different_published_archive_fails_as_a_conflict(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A published version with other bytes cannot be resumed."""
    digest_rig.registry.published["beta"] = OTHER_ARCHIVE

    with pytest.raises(PublishedArchiveConflictError) as excinfo:
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    message = str(excinfo.value)
    assert "Crate beta 0.1.0 is already published" in message, (
        "the conflict must name the crate"
    )
    assert digest(PLAIN_ARCHIVE) in message, (
        "the conflict must show the expected digest"
    )
    assert digest(OTHER_ARCHIVE) in message, (
        "the conflict must show the published digest"
    )
    assert digest_rig.cargo.crates_run("publish") == ["alpha"], "beta is not published"


@pytest.mark.parametrize(
    ("entries", "expected_text"),
    [
        ({"alpha": PLAIN_ARCHIVE, "beta": PLAIN_ARCHIVE}, "no entry for gamma-0.1.0"),
        (
            dict.fromkeys(("alpha", "beta", "gamma", "delta"), PLAIN_ARCHIVE),
            "delta-0.1.0.crate, which is not in the plan",
        ),
    ],
    ids=["missing_entry", "extra_entry"],
)
def test_digest_file_that_disagrees_with_the_plan_fails_before_staging(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    entries: dict[str, bytes],
    expected_text: str,
) -> None:
    """Nothing is staged, packaged, or uploaded when the file is wrong."""
    path = digest_rig.digests_file(entries)

    with pytest.raises(ExpectedDigestsError, match=expected_text):
        digest_rig.run(monkeypatch, live=True, expect_digests=path)

    assert digest_rig.cargo.calls == [], "no cargo command may have run"
    assert not digest_rig.build_directory.exists(), "nothing may have been staged"


@pytest.mark.parametrize("problem", ["malformed", "unreadable"])
def test_unusable_digest_file_fails_before_anything_else(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    """A malformed or missing file is reported before the pre-flight even runs."""
    path = digest_rig.root / "digests.txt"
    if problem == "malformed":
        path.write_text("garbage\n", encoding="utf-8")

    def _preflight_must_not_run(*_args: object, **_kwargs: object) -> None:
        """Fail if the pre-flight is reached."""
        pytest.fail("the digest file must be validated before the pre-flight")

    monkeypatch.setattr(
        "lading.commands.publish_preflight._run_preflight_checks",
        _preflight_must_not_run,
    )
    # The rig replaces the pre-flight with a no-op, so call the command directly.
    with pytest.raises(ExpectedDigestsError):
        publish.run(
            digest_rig.root,
            None,
            digest_rig.workspace(),
            options=publish.PublishOptions(
                build_directory=digest_rig.build_directory,
                command_runner=digest_rig.cargo,
                expect_digests=path,
            ),
        )

    assert digest_rig.cargo.calls == [], (
        "no cargo command may run before the digest file is validated"
    )


def test_without_the_option_nothing_changes(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No digests: no ``--locked``, no metadata query, no network, no summary."""
    summary = digest_rig.run(monkeypatch, live=True)

    assert digest_rig.cargo.subcommands("metadata") == [], (
        "Cargo is not asked for its target"
    )
    assert digest_rig.registry.requests == [], "crates.io is not consulted"
    assert ("cargo", "package", "--allow-dirty") in digest_rig.cargo.subcommands(
        "package"
    ), "cargo package keeps exactly its historical flags"
    assert all("--locked" not in command for command, _ in digest_rig.cargo.calls), (
        "no cargo command may carry --locked without the option"
    )
    assert "Archive checks" not in summary, (
        "the summary must not report archive checks without the option"
    )


def test_published_version_cargo_reports_after_the_check_is_compared(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version Cargo reports as existing is compared, not silently skipped."""
    answers = iter([None, digest(PLAIN_ARCHIVE)])
    digest_rig.cargo.failing["publish", "alpha"] = (101, _ALREADY_EXISTS)

    digest_rig.run(
        monkeypatch,
        live=True,
        expect_digests=digest_rig.digests_file(),
        published_digest_fetcher=lambda *_args: next(answers, None),
    )

    assert digest_rig.cargo.crates_run("publish") == LIVE_ORDER, "alpha was attempted"


def test_published_version_cargo_reports_with_other_bytes_is_a_conflict(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compare after Cargo's report fails as a conflict when bytes differ."""
    answers = iter([None, digest(OTHER_ARCHIVE)])
    digest_rig.cargo.failing["publish", "alpha"] = (101, _ALREADY_EXISTS)

    with pytest.raises(PublishedArchiveConflictError):
        digest_rig.run(
            monkeypatch,
            live=True,
            expect_digests=digest_rig.digests_file(),
            published_digest_fetcher=lambda *_args: next(answers, None),
        )


def test_published_version_that_cannot_be_fetched_stops_the_run(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cargo says it exists but crates.io serves nothing: refuse to guess."""
    digest_rig.cargo.failing["publish", "alpha"] = (101, _ALREADY_EXISTS)

    with pytest.raises(ArchiveUnavailableError, match="cannot be compared"):
        digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())


def test_each_outcome_is_counted(
    digest_rig: PublishRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive-check metric carries one increment per crate outcome."""
    metrics.reset()
    digest_rig.registry.published["alpha"] = PLAIN_ARCHIVE

    digest_rig.run(monkeypatch, live=True, expect_digests=digest_rig.digests_file())

    assert (
        metrics.counter_value(ARCHIVE_CHECK_METRIC, outcome="already_published") == 1
    ), "alpha must be counted as already published"
    assert metrics.counter_value(ARCHIVE_CHECK_METRIC, outcome="verified") == 4, (
        "alpha is skipped before packaging, so only beta and gamma are "
        "verified, twice each: once packaged, once uploaded"
    )
