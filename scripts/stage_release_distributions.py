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
    """Raised when the package index cannot be read safely."""


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
        raise ProjectIndexError(message)
    files = body.get("files")
    if not isinstance(files, list):
        message = "Project index response has no files list"
        raise ProjectIndexError(message)
    return typ.cast("list[object]", files)


def _project_filename(entry: object) -> str | None:
    """Return one valid distribution filename from an index entry."""
    if not isinstance(entry, dict):
        return None
    filename = entry.get("filename")
    if isinstance(filename, str):
        return filename
    return None


def _project_filenames(response: typ.IO[bytes]) -> set[str]:
    """Decode file names from one successful PEP 691 response."""
    body = json.load(response)
    return {
        filename
        for entry in _project_file_entries(body)
        if (filename := _project_filename(entry)) is not None
    }


def _read_index_request(request: urllib.request.Request) -> set[str]:
    """Fetch and decode one successful PEP 691 project response."""
    # The request is created by _build_index_request after its scheme check.
    with urllib.request.urlopen(  # ruff: ignore[suspicious-url-open-usage]
        request, timeout=_REQUEST_TIMEOUT_SECONDS
    ) as response:
        if response.status != _HTTP_OK:
            message = f"Project index returned HTTP {response.status}"
            raise ProjectIndexError(message)
        return _project_filenames(response)


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
    raise ProjectIndexError(message) from error


def _raise_network_error(error: OSError, retries: int) -> typ.NoReturn:
    """Raise a useful error after all requests to the project index fail."""
    message = f"Could not read project index after {retries} attempts: {error}"
    raise ProjectIndexError(message) from error


def _index_request_attempt(
    request: urllib.request.Request, *, attempt: int, retries: int
) -> set[str] | None:
    """Return one request result, retrying only eligible transient errors."""
    try:
        return _read_index_request(request)
    except urllib.error.HTTPError as error:
        return _handle_http_error(error, attempt=attempt, retries=retries)
    except (OSError, TimeoutError) as error:
        if attempt + 1 == retries:
            _raise_network_error(error, retries)
    return None


def _request_index(url: str, *, retries: int, retry_delay: float) -> set[str]:
    """Return names present in the project index, retrying transient failures."""
    _validate_retry_policy(retries, retry_delay)
    request = _build_index_request(url)
    for attempt in range(retries):
        project_files = _index_request_attempt(
            request, attempt=attempt, retries=retries
        )
        if project_files is not None:
            return project_files
        if retry_delay:
            time.sleep(retry_delay)
    message = "Index request retry loop ended unexpectedly"
    raise AssertionError(message)


def _staging_paths(directory: Path) -> list[Path]:
    """Read staging paths in stable order."""
    try:
        return sorted(directory.iterdir())
    except OSError as error:
        message = f"Could not read staging directory {directory}: {error}"
        raise OSError(message) from error


def _remove_published_file(path: Path) -> None:
    """Remove one published file after reporting its skip notice."""
    print(f"::notice::Skipping already-published distribution {path.name}")
    try:
        path.unlink()
    except OSError as error:
        message = f"Could not remove already-published file {path}: {error}"
        raise OSError(message) from error


def _remaining_distributions(paths: list[Path], names: set[str]) -> list[Path]:
    """Remove published paths and return the new distributions."""
    remaining: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        if path.name in names:
            _remove_published_file(path)
        else:
            remaining.append(path)
    return remaining


def stage_distributions(
    directory: Path,
    index_url: str,
    *,
    retries: int = 6,
    retry_delay: float = 2.0,
) -> tuple[Path, ...]:
    """Remove files whose exact filenames already occur in the project index.

    Parameters
    ----------
    directory : Path
        Flat directory holding the built sdist and wheels.
    index_url : str
        PEP 691 URL for the package's project page.
    retries : int
        Maximum number of requests for a transient index failure.
    retry_delay : float
        Seconds to wait between transient failures.

    Returns
    -------
    tuple[Path, ...]
        Local distributions whose names were absent from the index.

    Raises
    ------
    ProjectIndexError
        If the index cannot be read after retrying or returns an unexpected
        HTTP status.
    OSError
        If the staging directory cannot be read or an existing file cannot be
        removed.
    """
    try:
        names = _request_index(index_url, retries=retries, retry_delay=retry_delay)
    except ProjectIndexError as error:
        message = f"Could not stage project distributions: {error}"
        raise ProjectIndexError(message) from error
    try:
        paths = _staging_paths(directory)
        remaining = _remaining_distributions(paths, names)
    except OSError as error:
        raise OSError(str(error)) from error
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
        remaining = stage_distributions(
            directory, index_url, retries=retries, retry_delay=retry_delay
        )
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
