"""The live pipeline's failure boundary covers every archive-check failure.

``PublishError`` subclasses raised by the archive checks must reach the same
boundary as any other publish failure: the live pipeline logs which crate it
aborted on and how many were completed, and the publish command logs the
archive outcomes decided so far.
"""

import collections.abc as cabc
import dataclasses as dc
import logging

import pytest

from lading.commands.publish_errors import (
    PublishedArchiveConflictError,
    PublishError,
    UploadedArchiveMismatchError,
)
from lading.commands.publish_registry import (
    PublishedArchiveUnavailableError,
    PublishedDigestFetcher,
)
from tests.helpers.publish_digests import OTHER_ARCHIVE, PublishRig


def _lookup_fails_for_beta(rig: PublishRig) -> PublishedDigestFetcher:
    """Return a crates.io lookup that gives up for beta."""
    registry = rig.registry

    def fetch(name: str, version: str) -> str | None:
        """Fail beta's lookup and answer the rest from the fake registry."""
        if name == "beta":
            message = "Could not fetch the published archive for beta"
            raise PublishedArchiveUnavailableError(message)
        return registry(name, version)

    return fetch


def _beta_published_differently(rig: PublishRig) -> PublishedDigestFetcher:
    """Put a different archive for beta on the fake crates.io."""
    rig.registry.published["beta"] = OTHER_ARCHIVE
    return rig.registry


def _beta_uploaded_differently(rig: PublishRig) -> PublishedDigestFetcher:
    """Make the live ``cargo publish`` of beta upload other bytes."""
    rig.cargo.uploaded["beta"] = OTHER_ARCHIVE
    return rig.registry


@dc.dataclass(frozen=True, slots=True)
class _Failure:
    """How to provoke one archive-check failure, and what it records."""

    arrange: cabc.Callable[[PublishRig], PublishedDigestFetcher]
    error: type[PublishError]
    outcome: str


@pytest.mark.parametrize(
    "failure",
    [
        _Failure(
            _lookup_fails_for_beta, PublishedArchiveUnavailableError, "unavailable"
        ),
        _Failure(
            _beta_published_differently, PublishedArchiveConflictError, "conflict"
        ),
        _Failure(_beta_uploaded_differently, UploadedArchiveMismatchError, "mismatch"),
    ],
    ids=["lookup_unavailable", "published_conflict", "uploaded_mismatch"],
)
def test_archive_publish_errors_log_the_abort_and_the_outcomes(
    digest_rig: PublishRig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: _Failure,
) -> None:
    """The abort context and the archive summary are logged for each failure."""
    caplog.set_level(logging.ERROR)
    fetcher = failure.arrange(digest_rig)

    with pytest.raises(failure.error):
        digest_rig.run(
            monkeypatch,
            live=True,
            expect_digests=digest_rig.digests_file(),
            published_digest_fetcher=fetcher,
        )

    messages = caplog.messages
    assert any(
        message.startswith("Live pipeline: aborted on crate beta — 1/3")
        for message in messages
    ), "the live pipeline names the crate it aborted on"
    report = next(m for m in messages if m.startswith("Archive checks against"))
    assert "- alpha 0.1.0: verified" in report, "earlier outcomes are reported"
    assert f"- beta 0.1.0: {failure.outcome}" in report, "the failing crate is reported"
