"""Fetch the SHA-256 of an archive already published on crates.io.

``lading publish --expect-digests`` must tell a rerun of an interrupted
release (the version is on crates.io with the bytes the caller expects) from a
different upload of the same version. Cargo cannot say which: it only reports
that the version exists. This module downloads the published archive from the
crates.io static archive host and returns its digest.

The download is the only network access lading itself makes during a publish,
so it is a narrow, injectable port: :data:`PublishedDigestFetcher` takes a
crate name and version and returns a digest or :data:`None`. Production code
uses :func:`fetch_crates_io_digest`; tests pass a fake and never touch the
network. The archive is hashed as it streams in and is never held whole, and
the read is bounded, so a hostile or broken response cannot exhaust memory.

No credential is involved: the archive host is public, and the request carries
nothing but a generic ``User-Agent``.

Examples
--------
>>> crates_io_archive_url("alpha", "0.1.0")
'https://static.crates.io/crates/alpha/alpha-0.1.0.crate'
"""

import collections.abc as cabc
import contextlib
import dataclasses as dc
import hashlib
import http
import http.client
import logging
import re
import time
import typing as typ
import urllib.error
import urllib.request

from lading.commands.publish_errors import PublishError
from lading.commands.publish_registry_reads import (
    REQUEST_TIMEOUT_SECONDS,
    BudgetExhaustedError,
    Deadline,
    Response,
    declared_length,
    read_chunk,
)

LOGGER = logging.getLogger(__name__)

_ARCHIVE_HOST = "https://static.crates.io/crates"
_USER_AGENT = "lading"
# One budget for the whole lookup: connecting, every read, the waits between
# retries, and closing. Each blocking read waits at most what is left, and a
# result that arrives after expiry is rejected; a ``read1`` or a close already
# in progress cannot be interrupted, so the bound is not strict.
_TOTAL_BUDGET_SECONDS = 180.0
_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 2.0
# crates.io accepts uploads of 10 MiB unless a crate is granted more. A cap
# well above that still stops an endless response from filling the disk.
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_RETRYABLE_STATUSES = frozenset({
    http.HTTPStatus.TOO_MANY_REQUESTS,
    http.HTTPStatus.INTERNAL_SERVER_ERROR,
    http.HTTPStatus.BAD_GATEWAY,
    http.HTTPStatus.SERVICE_UNAVAILABLE,
    http.HTTPStatus.GATEWAY_TIMEOUT,
})
# What Cargo itself accepts, so a value outside it cannot be a published crate
# and must never be interpolated into a URL.
_CRATE_NAME = re.compile(r"[A-Za-z0-9_-]+")
_CRATE_VERSION = re.compile(r"[0-9A-Za-z.+-]+")


class PublishedDigestFetcher(typ.Protocol):
    """Port that reports the digest of a crate version's published archive."""

    def __call__(self, name: str, version: str) -> str | None:
        """Return the published archive's SHA-256, or ``None`` if not published.

        Raises
        ------
        PublishedArchiveUnavailableError
            If the archive cannot be fetched or hashed, so whether the version
            is published is unknown. A fetcher never reports that as ``None``.
        """


class PublishedArchiveUnavailableError(PublishError):
    """Raise when the published archive cannot be fetched or hashed.

    This is neither "not published" nor a mismatch: lading cannot tell whether
    the version is safe to skip, so it stops instead of guessing.
    """


class _Opener(typ.Protocol):
    """Callable that opens a request and yields a readable response."""

    def __call__(
        self, request: urllib.request.Request, /, *, timeout: float
    ) -> contextlib.AbstractContextManager[Response]:
        """Open ``request`` and return a context manager over its response."""


def crates_io_archive_url(name: str, version: str) -> str:
    """Return the static-host URL of a published crate archive.

    Parameters
    ----------
    name : str
        The crate's package name.
    version : str
        The crate's version.

    Returns
    -------
    str
        The ``https`` URL of ``<name>-<version>.crate``.

    Raises
    ------
    PublishedArchiveUnavailableError
        If ``name`` or ``version`` could not be a crates.io crate, which would
        otherwise let it alter the URL's path.

    Examples
    --------
    >>> crates_io_archive_url("wireframe_testing", "0.4.0-alpha1")
    'https://static.crates.io/crates/wireframe_testing/wireframe_testing-0.4.0-alpha1.crate'
    """
    if not _CRATE_NAME.fullmatch(name) or not _CRATE_VERSION.fullmatch(version):
        message = f"Cannot look up crate {name!r} version {version!r} on crates.io"
        raise PublishedArchiveUnavailableError(message)
    return f"{_ARCHIVE_HOST}/{name}/{name}-{version}.crate"


def _hash_response(response: Response, *, url: str, deadline: Deadline) -> str:
    """Hash ``response`` as it streams, within the size cap and the time budget.

    Returns
    -------
    str
        The lowercase hex SHA-256 of the whole body.

    Raises
    ------
    PublishedArchiveUnavailableError
        If the body exceeds the size cap.
    http.client.IncompleteRead
        If fewer bytes arrived than ``Content-Length`` promised, so the caller
        retries rather than trusting a truncated archive.
    """
    digest = hashlib.sha256()
    total = 0
    while chunk := read_chunk(response, deadline):
        total += len(chunk)
        if total > _MAX_ARCHIVE_BYTES:
            message = f"Published archive at {url} exceeds {_MAX_ARCHIVE_BYTES} bytes"
            raise PublishedArchiveUnavailableError(message)
        digest.update(chunk)
    declared = declared_length(response)
    if declared is not None and total < declared:
        nothing_received_back = b""
        raise http.client.IncompleteRead(nothing_received_back, declared - total)
    return digest.hexdigest()


@dc.dataclass(frozen=True, slots=True)
class FetchTransport:
    """The seams :func:`fetch_crates_io_digest` reaches the network through.

    Attributes
    ----------
    opener : _Opener
        Opens a request; tests substitute a fake so nothing touches the network.
    sleep : Callable[[float], None]
        Waits between retries; tests substitute a recorder.
    clock : Callable[[], float]
        Monotonic clock the total time budget is measured with.
    """

    # The URL handed to it is always built by `crates_io_archive_url`: a fixed
    # https host plus a validated name and version.
    opener: _Opener = urllib.request.urlopen  # ruff: ignore[suspicious-url-open-usage] -- fixed https host
    sleep: cabc.Callable[[float], None] = time.sleep
    clock: cabc.Callable[[], float] = time.monotonic


def _unavailable(
    url: str, error: BaseException | None
) -> PublishedArchiveUnavailableError:
    """Return the error for a fetch that gave up."""
    return PublishedArchiveUnavailableError(
        f"Could not fetch the published archive {url}: {error}"
    )


def _is_retryable(error: BaseException) -> bool:
    """Whether ``error`` is a transient failure worth another attempt."""
    match error:
        case BudgetExhaustedError():
            result = False
        case urllib.error.HTTPError():
            result = error.code in _RETRYABLE_STATUSES
        case OSError() | http.client.IncompleteRead():
            result = True
        case _:
            result = False
    return result


@dc.dataclass(frozen=True, slots=True)
class _Retry:
    """What a retry decision needs: where we are in the attempts and the budget."""

    url: str
    attempt: int
    deadline: Deadline
    transport: FetchTransport

    def wait_for_next(self, error: BaseException) -> bool:
        """Log and wait before the next attempt, if one is allowed.

        Returns
        -------
        bool
            ``False`` when attempts or the time budget are spent.
        """
        if (
            self.attempt >= _ATTEMPTS
            or self.deadline.remaining() <= _RETRY_DELAY_SECONDS
        ):
            return False
        LOGGER.warning(
            "Fetching %s failed on attempt %d of %d (%s); retrying",
            self.url,
            self.attempt,
            _ATTEMPTS,
            error,
        )
        # Never wait longer than the budget has left.
        self.transport.sleep(min(_RETRY_DELAY_SECONDS, self.deadline.remaining()))
        return True


def _open_and_hash(
    url: str, transport: FetchTransport, deadline: Deadline
) -> str | None:
    """Make one request; return the digest, or ``None`` for a 404."""
    # The URL comes from `crates_io_archive_url`: a fixed https host.
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage] -- fixed https host
        url, headers={"User-Agent": _USER_AGENT}
    )
    timeout = min(REQUEST_TIMEOUT_SECONDS, deadline.remaining())
    try:
        with transport.opener(request, timeout=timeout) as response:
            deadline.check()  # Connecting and reading headers used the budget too.
            return _hash_response(response, url=url, deadline=deadline)
    except urllib.error.HTTPError as error:
        # The error owns the response, which `urlopen` raised before the `with`
        # could close it; the status and headers stay readable once closed.
        error.close()
        if error.code == http.HTTPStatus.NOT_FOUND:
            return None
        raise
    except OSError as error:
        # A timeout while connecting or reading headers that spent the budget is
        # the budget expiring, whatever urllib wrapped it in; it is never a 404.
        if not isinstance(error, BudgetExhaustedError):
            deadline.check()
        raise


def _attempt(url: str, transport: FetchTransport, deadline: Deadline) -> str | None:
    """Make one request within the budget; return the digest, or ``None``."""
    deadline.check()
    result = _open_and_hash(url, transport, deadline)
    # Reads are bounded one by one and closing not at all, so a late end of
    # body, a late 404 or a slow close is the budget expiring, never a result.
    deadline.check()
    return result


def fetch_crates_io_digest(
    name: str, version: str, *, transport: FetchTransport | None = None
) -> str | None:
    """Return the SHA-256 of ``name`` ``version`` as published on crates.io.

    Transient failures (HTTP 429 and 5xx, and network errors) are retried a
    few times. A 404 means the version is not published and returns ``None``.

    Parameters
    ----------
    name : str
        The crate's package name.
    version : str
        The crate's version.
    transport : FetchTransport | None
        Test seam for the network and the retry delay; production leaves it
        unset.

    Returns
    -------
    str | None
        The lowercase hex digest of the published archive, or ``None`` when
        crates.io has no such version.

    Raises
    ------
    PublishedArchiveUnavailableError
        If the archive cannot be fetched after retrying, the response is too
        large, or the crate name or version is not valid.
    """  # ruff: ignore[docstring-missing-exception, docstring-extraneous-exception]  # raised through _unavailable and the URL builder
    url = crates_io_archive_url(name, version)
    active = FetchTransport() if transport is None else transport
    deadline = Deadline(
        active.clock, active.clock() + _TOTAL_BUDGET_SECONDS, _TOTAL_BUDGET_SECONDS
    )
    failure: BaseException | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return _attempt(url, active, deadline)
        except (OSError, http.client.IncompleteRead) as error:
            if not _is_retryable(error):
                raise _unavailable(url, error) from error
            failure = error
            if not _Retry(url, attempt, deadline, active).wait_for_next(error):
                break
    raise _unavailable(url, failure) from failure
