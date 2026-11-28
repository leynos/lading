"""Unit tests for the crates.io archive digest fetcher."""

import contextlib
import http.client
import io
import typing as typ
import urllib.request

import pytest

from lading.commands import publish_registry, publish_registry_reads
from lading.commands.publish_registry import (
    PublishedArchiveUnavailableError,
    crates_io_archive_url,
    fetch_crates_io_digest,
)
from tests.helpers.publish_digests import PLAIN_ARCHIVE, digest
from tests.unit.publish.registry_doubles import Script, http_error

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def test_url_points_at_the_static_archive_host() -> None:
    """The URL is the CDN path Cargo itself downloads from."""
    assert crates_io_archive_url("alpha-beta_c", "1.2.3-rc.1+build") == (
        "https://static.crates.io/crates/alpha-beta_c/alpha-beta_c-1.2.3-rc.1+build.crate"
    ), "the path is the crate name, then <name>-<version>.crate"


@pytest.mark.parametrize(
    ("name", "version"),
    [("../etc", "1.0.0"), ("a", "1/../2"), ("a b", "1.0.0"), ("", "1.0.0"), ("a", "")],
    ids=["dotdot_name", "slash_version", "space", "empty_name", "empty_version"],
)
def test_url_refuses_names_that_could_alter_the_path(name: str, version: str) -> None:
    """A value outside Cargo's alphabet never reaches the URL."""
    with pytest.raises(PublishedArchiveUnavailableError, match="Cannot look up"):
        crates_io_archive_url(name, version)


def test_published_archive_is_hashed() -> None:
    """A 200 response yields the digest of the bytes served."""
    script = Script(PLAIN_ARCHIVE)

    assert fetch_crates_io_digest("a", "1.0.0", transport=script.transport()) == (
        digest(PLAIN_ARCHIVE)
    ), "the digest must be that of the bytes served"
    request = script.requests[0]
    assert request.full_url == "https://static.crates.io/crates/a/a-1.0.0.crate", (
        "the request must target the static archive host"
    )
    assert request.get_header("User-agent") == "lading", "a generic client name"
    assert request.get_header("Authorization") is None, "no credential is sent"


def test_unpublished_version_is_none() -> None:
    """A 404 means the version is not on crates.io, which is not an error."""
    script = Script(http_error(404))

    assert fetch_crates_io_digest("a", "1.0.0", transport=script.transport()) is None, (
        "a 404 means not published"
    )
    assert not script.sleeps, "a 404 is final and is not retried"


def test_transient_failures_are_retried_then_succeed() -> None:
    """A 503 and a dropped connection are retried with a pause between."""
    script = Script(http_error(503), ConnectionResetError("reset"), PLAIN_ARCHIVE)

    result = fetch_crates_io_digest("a", "1.0.0", transport=script.transport())

    assert result == digest(PLAIN_ARCHIVE), "the retried fetch must return the digest"
    assert len(script.sleeps) == 2, "one pause after each failed attempt"


def test_retries_are_bounded() -> None:
    """Giving up after the last attempt reports the failure, not a 404."""
    script = Script(*[http_error(500)] * 3)

    with pytest.raises(PublishedArchiveUnavailableError, match="Could not fetch"):
        fetch_crates_io_digest("a", "1.0.0", transport=script.transport())

    assert len(script.requests) == 3, "every attempt must be made before giving up"
    assert len(script.sleeps) == 2, "no pause after the final attempt"


def test_permanent_failure_is_not_retried() -> None:
    """A 403 will not improve by waiting."""
    script = Script(http_error(403))

    with pytest.raises(PublishedArchiveUnavailableError, match="403"):
        fetch_crates_io_digest("a", "1.0.0", transport=script.transport())

    assert len(script.requests) == 1, "a permanent failure must not be retried"


def test_oversized_response_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The body is bounded, so an endless response cannot fill the disk."""
    monkeypatch.setattr(publish_registry, "_MAX_ARCHIVE_BYTES", 16)
    monkeypatch.setattr(publish_registry_reads, "_READ_CHUNK_BYTES", 8)
    script = Script(b"x" * 17)

    with pytest.raises(PublishedArchiveUnavailableError, match="exceeds 16 bytes"):
        fetch_crates_io_digest("a", "1.0.0", transport=script.transport())


class _Body(io.BytesIO):
    """A response body that states a ``Content-Length``."""

    def __init__(self, payload: bytes, declared: int) -> None:
        """Serve ``payload`` while claiming ``declared`` bytes."""
        super().__init__(payload)
        self.headers = {"Content-Length": str(declared)}


def test_incomplete_read_is_retried_then_succeeds() -> None:
    """A connection cut mid-body is a transient failure like any other."""
    script = Script(http.client.IncompleteRead(b"par", 10), PLAIN_ARCHIVE)

    result = fetch_crates_io_digest("a", "1.0.0", transport=script.transport())

    assert result == digest(PLAIN_ARCHIVE), "the retry must return the full digest"
    assert len(script.sleeps) == 1, "one pause after the failed attempt"


def test_exhausted_incomplete_reads_keep_the_last_one_as_the_cause() -> None:
    """After the final attempt the failure is reported with its cause."""
    script = Script(*[http.client.IncompleteRead(b"", 5)] * 3)

    with pytest.raises(PublishedArchiveUnavailableError) as excinfo:
        fetch_crates_io_digest("a", "1.0.0", transport=script.transport())

    assert isinstance(excinfo.value.__cause__, http.client.IncompleteRead), (
        "the final IncompleteRead must be the cause"
    )


def test_a_short_body_against_content_length_is_retried() -> None:
    """A body shorter than ``Content-Length`` is never trusted."""

    class _Serve(Script):
        """Serve a truncated body first, then a complete one."""

        @contextlib.contextmanager
        def open_url(
            self, request: urllib.request.Request, *, timeout: float
        ) -> cabc.Generator[io.BytesIO]:
            """Replay the next step as a body with a declared length."""
            del timeout
            self.requests.append(request)
            step = typ.cast("bytes", self.next_step())
            yield _Body(step, len(PLAIN_ARCHIVE))

    script = _Serve(PLAIN_ARCHIVE[:5], PLAIN_ARCHIVE)

    result = fetch_crates_io_digest("a", "1.0.0", transport=script.transport())

    assert result == digest(PLAIN_ARCHIVE), "only the complete body is accepted"
    assert len(script.requests) == 2, "the truncated body must have been retried"
