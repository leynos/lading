#!/usr/bin/env python3
r"""Stage release distributions against the PyPI project index.

This standard-library-only command removes local distributions whose
filenames already appear in the project's PEP 691 index. The caller then uses
``uv publish`` with Trusted Publishing. A missing project page (HTTP 404) is
treated as an empty index so the first release can be published.

Configuration
-------------
DISTRIBUTION_DIRECTORY : str, optional
    Directory containing distributions. Defaults to ``dist/publish``.
PYPI_PROJECT : str, optional
    Project slug in the index URL. Defaults to ``lading``.
PYPI_INDEX_URL : str, optional
    Index root URL. Defaults to ``https://pypi.org``.
INDEX_RETRIES : int, optional
    Maximum attempts for transient failures. Defaults to ``6``.
INDEX_RETRY_DELAY : float, optional
    Delay between attempts, in seconds. Defaults to ``2.0``.
GITHUB_OUTPUT : str, optional
    Output file to which ``remaining=true`` or ``remaining=false`` is appended.

Examples
--------
Run from the repository root using the default project and staging directory:

    python scripts/stage_release_distributions.py

Exercise the command against a local Simple index:

    PYPI_INDEX_URL=http://127.0.0.1:8080 PYPI_PROJECT=demo \\
        DISTRIBUTION_DIRECTORY=build/dist \\
        python scripts/stage_release_distributions.py
"""

import collections.abc as cabc
import dataclasses as dc
import json
import os
import sys
import time
import typing as typ
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_ACCEPT = "application/vnd.pypi.simple.v1+json"
_REQUEST_TIMEOUT_SECONDS = 10
_HTTP_OK = 200
_HTTP_NOT_FOUND = 404
_RETRYABLE_STATUS = {429, *range(500, 600)}


class ProjectIndexError(RuntimeError):
    """Project-index failure with a bounded diagnostic and telemetry fields."""

    def __init__(
        self,
        message: str,
        *,
        failure_category: str = "invalid_response",
        status_code: int | None = None,
    ) -> None:
        """Record a bounded failure classification for workflow telemetry."""
        super().__init__(message)
        self.failure_category = failure_category
        self.status_code = status_code


class _IndexResponse(typ.Protocol):
    """Response surface needed to decode a PEP 691 project index."""

    status: int

    def read(self, size: int = -1, /) -> bytes:
        """Read response bytes."""
        ...

    def __enter__(self) -> typ.Self:
        """Enter the response's resource scope."""
        ...

    def __exit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> bool | None:
        """Close the response and propagate exceptions."""
        ...


_IndexOpener = cabc.Callable[..., _IndexResponse]
_Sleeper = cabc.Callable[[float], None]
_LogSink = cabc.Callable[[str], None]


@dc.dataclass(frozen=True, slots=True)
class _IndexOperations:
    """Optional effects used by the bounded project-index query."""

    opener: _IndexOpener | None = None
    sleeper: _Sleeper | None = None
    log: _LogSink | None = None


def _validate_retry_policy(retries: int, retry_delay: float) -> None:
    """Reject a retry policy that cannot make progress."""
    if retries < 1:
        message = "index retries must be positive"
        raise ValueError(message)
    if retry_delay < 0:
        message = "index retry delay cannot be negative"
        raise ValueError(message)


def _is_safe_index_url(url: str) -> bool:
    """Whether ``url`` uses HTTP(S), has a host, and carries no userinfo."""
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"http", "https"}:
        return False
    if not parsed_url.hostname:
        return False
    if parsed_url.username is not None:
        return False
    return parsed_url.password is None


def _build_index_request(url: str) -> urllib.request.Request:
    """Build a request only for an unauthenticated HTTP(S) index URL."""
    if not _is_safe_index_url(url):
        message = "Project index URL must be an unauthenticated HTTP(S) URL"
        raise ProjectIndexError(message)
    # S310 cannot infer the scheme allowlist checked above.
    return urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url, headers={"Accept": _ACCEPT}
    )


def _project_file_entries(body: object) -> list[object]:
    """Return the file entries from a structurally valid index response."""
    if not isinstance(body, dict):
        message = "Project index response is not an object"
        raise ProjectIndexError(message, failure_category="invalid_schema")
    files = body.get("files")
    if not isinstance(files, list):
        message = "Project index response has no files list"
        raise ProjectIndexError(message, failure_category="invalid_schema")
    return typ.cast("list[object]", files)


def _project_filename(entry: object) -> str | None:
    """Return one valid distribution filename from an index entry."""
    if not isinstance(entry, dict):
        return None
    filename = entry.get("filename")
    if isinstance(filename, str):
        return filename
    return None


def _project_filenames(response: _IndexResponse) -> set[str]:
    """Decode file names from one successful PEP 691 response."""
    body = json.load(response)
    return {
        filename
        for entry in _project_file_entries(body)
        if (filename := _project_filename(entry)) is not None
    }


def _open_index(request: urllib.request.Request, *, timeout: float) -> _IndexResponse:
    """Open an index request after its URL has passed scheme validation."""
    # _build_index_request rejects every scheme except unauthenticated HTTP(S).
    return urllib.request.urlopen(  # ruff: ignore[suspicious-url-open-usage]
        request, timeout=timeout
    )


def _read_index_request(
    request: urllib.request.Request, *, opener: _IndexOpener
) -> set[str]:
    """Fetch and decode one successful PEP 691 project response."""
    with opener(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        if response.status != _HTTP_OK:
            message = f"Project index returned HTTP {response.status}"
            raise ProjectIndexError(
                message,
                failure_category="http_status",
                status_code=response.status,
            )
        try:
            return _project_filenames(response)
        except json.JSONDecodeError as error:
            message = "Project index returned malformed JSON"
            raise ProjectIndexError(message, failure_category="invalid_json") from error


def _handle_http_error(
    error: urllib.error.HTTPError, *, attempt: int, retries: int
) -> set[str] | None:
    """Return the empty-index 404 result, retry, or raise a final HTTP error."""
    if error.code == _HTTP_NOT_FOUND:
        return set()
    if error.code in _RETRYABLE_STATUS and attempt + 1 < retries:
        return None
    if error.code in _RETRYABLE_STATUS:
        message = f"Project index returned HTTP {error.code} after {retries} attempts"
    else:
        message = f"Project index returned HTTP {error.code}"
    raise ProjectIndexError(
        message, failure_category="http_status", status_code=error.code
    ) from error


def _raise_network_error(error: OSError, retries: int) -> typ.NoReturn:
    """Raise a useful error after all requests to the project index fail."""
    message = f"Could not read project index after {retries} attempts"
    raise ProjectIndexError(message, failure_category="network") from error


def _index_request_attempt(
    request: urllib.request.Request,
    *,
    attempt: int,
    retries: int,
    opener: _IndexOpener,
) -> tuple[set[str] | None, str | None]:
    """Return one request result, retrying only eligible transient errors."""
    try:
        return _read_index_request(request, opener=opener), None
    except urllib.error.HTTPError as error:
        project_files = _handle_http_error(error, attempt=attempt, retries=retries)
        if project_files is not None:
            return project_files, None
        return None, f"HTTP {error.code}"
    except (OSError, TimeoutError) as error:
        if attempt + 1 == retries:
            _raise_network_error(error, retries)
    return None, "network error"


def _request_index(
    url: str,
    *,
    retries: int,
    retry_delay: float,
    operations: _IndexOperations | None = None,
) -> set[str]:
    """Return names present in the project index, retrying transient failures."""
    _validate_retry_policy(retries, retry_delay)
    request = _build_index_request(url)
    if operations is None:
        operations = _IndexOperations()
    open_index = _open_index if operations.opener is None else operations.opener
    sleep = time.sleep if operations.sleeper is None else operations.sleeper
    write_log = print if operations.log is None else operations.log
    started_at = time.monotonic()
    for attempt in range(retries):
        try:
            project_files, retry_reason = _index_request_attempt(
                request, attempt=attempt, retries=retries, opener=open_index
            )
        except ProjectIndexError as error:
            elapsed_ms = round((time.monotonic() - started_at) * 1000)
            write_log(
                "::notice::Project index lookup "
                f"(outcome=failed, attempts={attempt + 1}, retries={attempt}, "
                f"category={error.failure_category}, "
                f"status={error.status_code or 'none'}, elapsed_ms={elapsed_ms})"
            )
            raise
        if project_files is not None:
            elapsed_ms = round((time.monotonic() - started_at) * 1000)
            write_log(
                "::notice::Project index lookup "
                f"(outcome=success, attempts={attempt + 1}, retries={attempt}, "
                f"elapsed_ms={elapsed_ms})"
            )
            return project_files
        write_log(
            f"::notice::Retrying project index lookup after {retry_reason} "
            f"(attempt={attempt + 1}/{retries - 1}, delay_seconds={retry_delay})"
        )
        if retry_delay:
            sleep(retry_delay)
    message = "Index request retry loop ended unexpectedly"
    raise AssertionError(message)


def _staging_paths(directory: Path) -> list[Path]:
    """Read staging paths in stable order."""
    try:
        return sorted(directory.iterdir())
    except OSError as error:
        message = f"Could not read staging directory {directory}: {error}"
        raise OSError(message) from error


def _remove_published_file(
    path: Path,
    *,
    remove_file: cabc.Callable[[Path], None],
    log: _LogSink,
) -> None:
    """Report and remove one already-published distribution."""
    log(f"::notice::Skipping already-published distribution {path.name}")
    try:
        remove_file(path)
    except OSError as error:
        message = f"Could not remove already-published file {path}: {error}"
        raise OSError(message) from error


def _remaining_distributions(
    paths: list[Path],
    names: set[str],
    *,
    remove_file: cabc.Callable[[Path], None],
    log: _LogSink,
) -> list[Path]:
    """Remove published paths and return the new distributions."""
    remaining: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        if path.name in names:
            _remove_published_file(path, remove_file=remove_file, log=log)
        else:
            remaining.append(path)
    return remaining


def stage_distributions(
    directory: Path,
    published_filenames: cabc.Collection[str],
    *,
    remove_file: cabc.Callable[[Path], None] | None = None,
    log: _LogSink | None = None,
) -> tuple[Path, ...]:
    """Remove files whose exact filenames already occur in the project index.

    Parameters
    ----------
    directory : Path
        Flat directory holding the built sdist and wheels.
    published_filenames : Collection[str]
        Exact distribution filenames currently present in the index.
    remove_file : Callable[[Path], None], optional
        Operation used to remove an indexed distribution. Defaults to unlink.
    log : Callable[[str], None], optional
        Sink for per-file skip notices. Defaults to standard output.

    Returns
    -------
    tuple[Path, ...]
        Local distributions whose names were absent from the index.

    """
    unlink = Path.unlink if remove_file is None else remove_file
    write_log = print if log is None else log
    paths = _staging_paths(directory)
    remaining = _remaining_distributions(
        paths,
        set(published_filenames),
        remove_file=unlink,
        log=write_log,
    )
    return tuple(remaining)


def main() -> int:
    """Stage ``dist/publish`` against the configured project index."""
    directory = Path(os.environ.get("DISTRIBUTION_DIRECTORY", "dist/publish"))
    project = os.environ.get("PYPI_PROJECT", "lading")
    index_root = os.environ.get("PYPI_INDEX_URL", "https://pypi.org").rstrip("/")
    index_url = f"{index_root}/simple/{project}/"
    try:
        retries = int(os.environ.get("INDEX_RETRIES", "6"))
        retry_delay = float(os.environ.get("INDEX_RETRY_DELAY", "2"))
        published_filenames = _request_index(
            index_url, retries=retries, retry_delay=retry_delay
        )
        remaining = stage_distributions(directory, published_filenames)
    except (ProjectIndexError, OSError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"remaining={'true' if remaining else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
