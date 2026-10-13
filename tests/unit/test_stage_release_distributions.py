"""Unit contracts for Simple-index status and exact-name staging."""

import io
import json
import types
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from tests.helpers.script_imports import import_script_module


@pytest.fixture(name="stage_module")
def stage_module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the stdlib-only staging module from ``scripts``."""
    return import_script_module(monkeypatch, "stage_release_distributions")


def test_filename_matching_is_exact_and_reports_each_skipped_file(
    stage_module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only complete filenames in the index are deleted from the directory."""
    directory = tmp_path / "publish"
    directory.mkdir()
    already_published = directory / "lading-1.0.tar.gz"
    same_version_other_format = directory / "lading-1.0-py3-none-any.whl"
    future_version = directory / "lading-1.1.tar.gz"
    for path in (already_published, same_version_other_format, future_version):
        path.touch()
    monkeypatch.setattr(
        stage_module,
        "_request_index",
        lambda *_args, **_kwargs: {already_published.name},
    )

    remaining = stage_module.stage_distributions(
        directory, "https://index.test/simple/lading/"
    )

    assert remaining == (same_version_other_format, future_version), (
        "only the exact indexed filename should be removed"
    )
    assert not already_published.exists(), "the indexed distribution should be removed"
    assert (
        f"Skipping already-published distribution {already_published.name}"
        in capsys.readouterr().out
    ), "each removed distribution should be announced"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, set()), (403, "error")],
    ids=["not-published", "other-http-error"],
)
def test_project_index_status_distinguishes_missing_from_other_errors(
    stage_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: set[str] | str,
) -> None:
    """A 404 is an empty index, while another 4xx status fails publication."""

    def raise_status(*_args: object, **_kwargs: object) -> object:
        url = "https://index.test"
        reason = "failure"
        raise urllib.error.HTTPError(url, status, reason, Message(), None)

    monkeypatch.setattr(stage_module.urllib.request, "urlopen", raise_status)
    if expected == "error":
        with pytest.raises(stage_module.ProjectIndexError, match="HTTP 403"):
            stage_module._request_index("https://index.test", retries=3, retry_delay=0)
    else:
        assert (
            stage_module._request_index("https://index.test", retries=3, retry_delay=0)
            == expected
        ), "a missing project index should be treated as empty"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://index.test/simple/", "https://user@host.test/"],
)
def test_project_index_requires_an_unauthenticated_http_url(
    stage_module: types.ModuleType, url: str
) -> None:
    """The project index boundary accepts only plain HTTP or HTTPS URLs."""
    with pytest.raises(stage_module.ProjectIndexError, match=r"HTTP\(S\) URL"):
        stage_module._request_index(url, retries=1, retry_delay=0)


def test_retryable_status_retries_then_reads_pep691_filenames(
    stage_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 503 retries, then a valid PEP 691 response returns exact filenames."""

    class JsonResponse(io.BytesIO):
        """File-like successful response with the status urllib exposes."""

        status = 200

    responses: list[object] = [
        urllib.error.HTTPError("https://index.test", 503, "busy", Message(), None),
        JsonResponse(
            json.dumps({"files": [{"filename": "lading-1.0.tar.gz"}]}).encode()
        ),
    ]
    calls = 0

    def urlopen(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        response = responses.pop(0)
        if isinstance(response, urllib.error.HTTPError):
            raise response
        return response

    monkeypatch.setattr(stage_module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(stage_module.time, "sleep", lambda _delay: None)

    assert stage_module._request_index(
        "https://index.test", retries=2, retry_delay=0
    ) == {"lading-1.0.tar.gz"}, "the successful response should expose its filenames"
    assert calls == 2, (
        "the retryable error should be followed by one successful request"
    )
