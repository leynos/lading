"""Unit contracts for Simple-index status and exact-name staging."""

import io
import json
import string
import tempfile
import types
import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.helpers.script_imports import import_script_module

_FILENAME_CHARACTERS = string.ascii_lowercase + string.digits + "-_."


def _filename_strategy() -> st.SearchStrategy[str]:
    """Generate safe single-component filenames, excluding dot directories."""
    first = st.sampled_from(string.ascii_lowercase + string.digits)
    rest = st.text(alphabet=_FILENAME_CHARACTERS, max_size=19)
    return st.tuples(first, rest).map("".join)


@pytest.fixture(name="stage_module")
def stage_module_fixture(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import the stdlib-only staging module from ``scripts``."""
    return import_script_module(monkeypatch, "stage_release_distributions")


class _JsonResponse(io.BytesIO):
    """File-like successful response with the status urllib exposes."""

    status = 200


def _http_error(status: int) -> urllib.error.HTTPError:
    """Build a repeatable HTTP response failure for the injected opener."""
    return urllib.error.HTTPError(
        "https://index.test", status, "failure", Message(), None
    )


def test_stage_removes_exact_index_filenames_and_reports_each_skip(
    stage_module: types.ModuleType,
    tmp_path: Path,
) -> None:
    """Only complete filenames in the index are deleted from the directory."""
    directory = tmp_path / "publish"
    directory.mkdir()
    already_published = directory / "lading-1.0.tar.gz"
    same_version_other_format = directory / "lading-1.0-py3-none-any.whl"
    future_version = directory / "lading-1.1.tar.gz"
    for path in (already_published, same_version_other_format, future_version):
        path.touch()
    removed: list[Path] = []
    messages: list[str] = []

    def remove_file(path: Path) -> None:
        removed.append(path)
        path.unlink()

    remaining = stage_module.stage_distributions(
        directory,
        {already_published.name},
        remove_file=remove_file,
        log=messages.append,
    )

    assert remaining == (same_version_other_format, future_version), (
        "only the exact indexed filename should be removed"
    )
    assert not already_published.exists(), "the indexed distribution should be removed"
    assert removed == [already_published], (
        "the injected remover should receive exact matches"
    )
    assert messages == [
        f"::notice::Skipping already-published distribution {already_published.name}"
    ], "each removed distribution should be announced through the injected sink"


@given(
    local_names=st.lists(_filename_strategy(), unique=True, max_size=8),
    indexed_names=st.sets(_filename_strategy(), max_size=8),
)
# The module is stateless and each generated case gets an isolated directory.
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_stage_filename_membership_property(
    stage_module: types.ModuleType,
    tmp_path: Path,
    local_names: list[str],
    indexed_names: set[str],
) -> None:
    """Staging retains exactly local files absent from the index by name."""
    with tempfile.TemporaryDirectory(dir=tmp_path) as scratch:
        directory = Path(scratch)
        for name in local_names:
            (directory / name).touch()

        remaining = stage_module.stage_distributions(directory, indexed_names)

        expected = tuple(
            directory / name for name in sorted(set(local_names) - indexed_names)
        )
        assert remaining == expected, (
            "staging must compare complete filenames and preserve stable ordering"
        )
        assert sorted(path.name for path in directory.iterdir()) == sorted(
            set(local_names) - indexed_names
        ), "staging must delete every published filename and retain all other files"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, set()), (403, "error")],
    ids=["not-published", "other-http-error"],
)
def test_project_index_status_distinguishes_missing_from_other_errors(
    stage_module: types.ModuleType,
    status: int,
    expected: set[str] | str,
) -> None:
    """A 404 is an empty index, while another 4xx status fails publication."""

    def open_index(*_args: object, **_kwargs: object) -> _JsonResponse:
        raise _http_error(status)

    if expected == "error":
        with pytest.raises(stage_module.ProjectIndexError, match="HTTP 403"):
            stage_module._request_index(
                "https://index.test",
                retries=3,
                retry_delay=0,
                operations=stage_module._IndexOperations(opener=open_index),
            )
    else:
        assert (
            stage_module._request_index(
                "https://index.test",
                retries=3,
                retry_delay=0,
                operations=stage_module._IndexOperations(opener=open_index),
            )
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


@pytest.mark.parametrize(
    ("payload", "category"),
    [(b"not json", "invalid_json"), (b'{"files": {}}', "invalid_schema")],
    ids=["malformed-json", "invalid-schema"],
)
def test_malformed_index_responses_fail_without_retry(
    stage_module: types.ModuleType, payload: bytes, category: str
) -> None:
    """Malformed PEP 691 data fails as a bounded, classified index error."""

    def open_index(*_args: object, **_kwargs: object) -> _JsonResponse:
        return _JsonResponse(payload)

    with pytest.raises(stage_module.ProjectIndexError) as raised:
        stage_module._request_index(
            "https://index.test",
            retries=4,
            retry_delay=0,
            operations=stage_module._IndexOperations(opener=open_index),
        )

    assert raised.value.failure_category == category, (
        "malformed index data should expose a stable failure category"
    )


def test_transient_status_retries_then_logs_bounded_success_telemetry(
    stage_module: types.ModuleType,
) -> None:
    """A retry then success reports attempts without exposing the index URL."""
    responses: list[_JsonResponse | BaseException] = [
        _http_error(503),
        _JsonResponse(
            json.dumps({"files": [{"filename": "lading-1.0.tar.gz"}]}).encode()
        ),
    ]
    delays: list[float] = []
    messages: list[str] = []

    def open_index(*_args: object, **_kwargs: object) -> _JsonResponse:
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    filenames = stage_module._request_index(
        "https://private.example/simple/lading/",
        retries=2,
        retry_delay=1,
        operations=stage_module._IndexOperations(
            opener=open_index, sleeper=delays.append, log=messages.append
        ),
    )

    assert filenames == {"lading-1.0.tar.gz"}, "the successful response must be decoded"
    assert delays == [1], "a transient failure should wait before its retry"
    assert (
        "Retrying project index lookup after HTTP 503 "
        "(attempt=1/1, delay_seconds=1)" in messages[0]
    ), "the retry notice should include its attempt and delay"
    assert "outcome=success, attempts=2, retries=1" in messages[1], (
        "the successful index query should report its bounded outcome summary"
    )
    assert "attempts=2, retries=1" in messages[1], (
        "success telemetry should report bounded retry counts"
    )
    assert "private.example" not in " ".join(messages), (
        "index telemetry must not disclose the configured URL"
    )


@given(
    transient_failures=st.integers(min_value=1, max_value=5),
    outcome=st.sampled_from(("success", "exhaustion")),
)
# All generated responses and counters are local to one example.
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_retry_sequence_property(
    stage_module: types.ModuleType, transient_failures: int, outcome: str
) -> None:
    """Retryable sequences stop at success or at the configured attempt limit."""
    succeeds = outcome == "success"
    responses: list[_JsonResponse | BaseException] = [
        *(_http_error(500) for _ in range(transient_failures)),
    ]
    if succeeds:
        responses.append(_JsonResponse(b'{"files": []}'))
    attempts = 0

    def open_index(*_args: object, **_kwargs: object) -> _JsonResponse:
        nonlocal attempts
        attempts += 1
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    retry_limit = transient_failures + int(succeeds)
    if succeeds:
        filenames = stage_module._request_index(
            "https://index.test",
            retries=retry_limit,
            retry_delay=0,
            operations=stage_module._IndexOperations(
                opener=open_index, log=lambda _message: None
            ),
        )
        assert filenames == set(), "the empty successful index should remain empty"
        assert attempts == transient_failures + 1, (
            "the lookup should stop at its first successful attempt"
        )
    else:
        with pytest.raises(stage_module.ProjectIndexError):
            stage_module._request_index(
                "https://index.test",
                retries=retry_limit,
                retry_delay=0,
                operations=stage_module._IndexOperations(
                    opener=open_index, log=lambda _message: None
                ),
            )
        assert attempts == transient_failures, (
            "the lookup must stop when its retry budget is exhausted"
        )


@pytest.mark.parametrize("failure", ["status", "timeout"])
def test_retryable_failure_exhaustion_is_reported(
    stage_module: types.ModuleType, failure: str
) -> None:
    """Exhausting status or network retries fails with bounded telemetry."""
    attempts = 0
    messages: list[str] = []

    def open_index(*_args: object, **_kwargs: object) -> _JsonResponse:
        nonlocal attempts
        attempts += 1
        if failure == "status":
            raise _http_error(500)
        raise TimeoutError

    with pytest.raises(stage_module.ProjectIndexError) as raised:
        stage_module._request_index(
            "https://private.example/simple/lading/",
            retries=3,
            retry_delay=0,
            operations=stage_module._IndexOperations(
                opener=open_index, log=messages.append
            ),
        )

    assert attempts == 3, "the configured maximum should bound retries"
    assert raised.value.failure_category in {"http_status", "network"}, (
        "terminal transport failures should expose a stable category"
    )
    assert "outcome=failed, attempts=3, retries=2" in messages[-1], (
        "failure telemetry should report the exhausted attempt budget"
    )
    assert "private.example" not in messages[-1], (
        "failure telemetry must not disclose the configured URL"
    )
