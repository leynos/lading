"""Run the checked-in distribution release steps against local stand-ins."""

import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from tests.workflow_contracts.step_harness import (
    REPOSITORY_ROOT,
    _find_step,
    _run_step,
    install_recording_command,
)

COLLECT_ACTION = (
    REPOSITORY_ROOT / ".github/actions/collect-release-artefacts/action.yml"
)
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/release.yml"


class _IndexServer:
    """Serve queued responses for the PEP 691 project endpoint."""

    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        """Configure a loopback server with the supplied response queue."""
        self.responses = responses
        self.requests: list[tuple[str, str | None]] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests.append((self.path, self.headers.get("Accept")))
                status, body = owner.responses[
                    min(len(owner.requests) - 1, len(owner.responses) - 1)
                ]
                self.send_response(status)
                self.send_header("Content-Type", "application/vnd.pypi.simple.v1+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
                """Suppress access logging from this loopback test server."""
                del code, size

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _IndexServer:
        """Start the server thread and return this local-index fixture."""
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop the server and wait for its request thread to exit."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def root_url(self) -> str:
        """The loopback address for the local simple index."""
        return f"http://127.0.0.1:{self.server.server_port}"


def _index(files: tuple[str, ...]) -> bytes:
    """Encode a PEP 691 project response containing ``files``."""
    return json.dumps({"files": [{"filename": name} for name in files]}).encode()


def _run_staging_step(
    directory: Path, index_url: str, **overrides: str
) -> subprocess.CompletedProcess[str]:
    """Execute the workflow's stage command against one local index."""
    step = _find_step(
        RELEASE_WORKFLOW,
        "Skip distributions already published to PyPI",
        job="publish-pypi",
    )
    environment = {
        "PYPI_INDEX_URL": index_url,
        "DISTRIBUTION_DIRECTORY": str(directory / "dist/publish"),
        "INDEX_RETRY_DELAY": "0",
        **overrides,
    }
    return _run_step(step, directory, env=environment)


def _make_distribution(directory: Path, name: str) -> Path:
    """Write one named distribution under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"distribution")
    return path


def _stage_script_for_scratch_checkout(tmp_path: Path) -> None:
    """Copy the checked-in stage script to the shell step's scratch checkout."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(
        REPOSITORY_ROOT / "scripts/stage_release_distributions.py",
        scripts / "stage_release_distributions.py",
    )


@pytest.fixture(autouse=True)
def stage_script_in_scratch_checkout(tmp_path: Path) -> None:
    """Let checked-in workflow shell run its relative script in scratch."""
    _stage_script_for_scratch_checkout(tmp_path)


def test_collector_copies_the_sdist_and_every_wheel(tmp_path: Path) -> None:
    """Collection puts one sdist and every nested wheel in ``publish``."""
    dist = tmp_path / "dist"
    _make_distribution(dist / "wheels-pure/nested", "lading-1.0-py3-none-any.whl")
    _make_distribution(dist / "wheels-pure", "lading-1.0-py2.py3-none-any.whl")
    _make_distribution(dist / "wheels-pure", "lading-1.0.tar.gz")

    step = _find_step(COLLECT_ACTION, "Collect and validate release distributions")
    completed = _run_step(step, tmp_path, env={"DIST_DIRECTORY": str(dist)})

    assert completed.returncode == 0, completed.stderr
    assert sorted(path.name for path in (dist / "publish").iterdir()) == [
        "lading-1.0-py2.py3-none-any.whl",
        "lading-1.0-py3-none-any.whl",
        "lading-1.0.tar.gz",
    ], "collection should preserve the sdist and every nested wheel"


def test_collector_fails_when_the_sdist_is_missing(tmp_path: Path) -> None:
    """Release collection explains a missing sdist and lists downloaded files."""
    dist = tmp_path / "dist"
    _make_distribution(dist, "lading-1.0-py3-none-any.whl")
    step = _find_step(COLLECT_ACTION, "Collect and validate release distributions")

    completed = _run_step(step, tmp_path, env={"DIST_DIRECTORY": str(dist)})

    assert completed.returncode == 1, "collection must fail without the sdist"
    assert "Expected exactly one source distribution." in completed.stderr, (
        "the failure should name the sdist requirement"
    )
    assert "lading-1.0-py3-none-any.whl" in completed.stderr, (
        "the failure should list downloaded artefacts"
    )


def test_staging_skips_published_names_and_keeps_new_files(tmp_path: Path) -> None:
    """The checked-in stage step compares exact filenames and records output."""
    dist = tmp_path / "dist/publish"
    old_wheel = _make_distribution(dist, "lading-1.0-py3-none-any.whl")
    old_sdist = _make_distribution(dist, "lading-1.0.tar.gz")
    new_wheel = _make_distribution(dist, "lading-1.0-py2.py3-none-any.whl")
    output = tmp_path / "github-output"
    output.touch()
    with _IndexServer([(200, _index((old_wheel.name, old_sdist.name)))]) as index:
        completed = _run_staging_step(
            tmp_path, index.root_url, GITHUB_OUTPUT=str(output)
        )

    assert completed.returncode == 0, completed.stderr
    assert (
        f"Skipping already-published distribution {old_wheel.name}" in completed.stdout
    ), "the existing wheel should be reported"
    assert (
        f"Skipping already-published distribution {old_sdist.name}" in completed.stdout
    ), "the existing sdist should be reported"
    assert sorted(path.name for path in dist.iterdir()) == [new_wheel.name], (
        "only the unpublished distribution should remain"
    )
    assert "remaining=true" in output.read_text(encoding="utf-8"), (
        "the workflow output should signal an upload"
    )
    assert index.requests == [
        ("/simple/lading/", "application/vnd.pypi.simple.v1+json")
    ], "staging should use the PEP 691 project endpoint"


def test_staging_404_treats_a_new_project_as_empty(tmp_path: Path) -> None:
    """A 404 means the project has no published files and skips nothing."""
    dist = tmp_path / "dist/publish"
    wheel = _make_distribution(dist, "lading-1.0-py3-none-any.whl")
    output = tmp_path / "github-output"
    output.touch()
    with _IndexServer([(404, b"not found")]) as index:
        completed = _run_staging_step(
            tmp_path, index.root_url, GITHUB_OUTPUT=str(output)
        )

    assert completed.returncode == 0, completed.stderr
    assert list(dist.iterdir()) == [wheel], "a missing project should skip nothing"
    assert "remaining=true" in output.read_text(encoding="utf-8"), (
        "the workflow output should request publication"
    )


def test_staging_retries_transient_status_then_succeeds(tmp_path: Path) -> None:
    """A transient server failure is retried before staging proceeds."""
    dist = tmp_path / "dist/publish"
    _make_distribution(dist, "lading-1.0.tar.gz")
    with _IndexServer([(500, b"busy"), (200, _index(()))]) as index:
        completed = _run_staging_step(tmp_path, index.root_url)

    assert completed.returncode == 0, completed.stderr
    assert len(index.requests) == 2, "staging should retry the transient server error"


def test_staging_fails_after_retryable_http_errors_are_exhausted(
    tmp_path: Path,
) -> None:
    """An exhausted 500 response fails the step without waiting between tries."""
    dist = tmp_path / "dist/publish"
    _make_distribution(dist, "lading-1.0.tar.gz")
    with _IndexServer([(500, b"busy")]) as index:
        completed = _run_staging_step(tmp_path, index.root_url, INDEX_RETRIES="2")

    assert completed.returncode == 1, "staging must fail after exhausting retries"
    assert "HTTP 500 after 2 attempts" in completed.stderr, (
        "the failure should report the exhausted status and attempts"
    )
    assert len(index.requests) == 2, "the configured retry count should be honoured"


def test_staging_fails_on_non_retryable_http_error(tmp_path: Path) -> None:
    """An authorization error is reported immediately rather than ignored."""
    dist = tmp_path / "dist/publish"
    _make_distribution(dist, "lading-1.0.tar.gz")
    with _IndexServer([(403, b"forbidden")]) as index:
        completed = _run_staging_step(tmp_path, index.root_url)

    assert completed.returncode == 1, "a non-retryable index error must fail staging"
    assert "HTTP 403" in completed.stderr, "the status should be retained in the error"
    assert len(index.requests) == 1, "a non-retryable error should not be retried"


def test_publish_uploads_only_remaining_distributions(tmp_path: Path) -> None:
    """The PyPI step uploads only staged files with OIDC and index checking."""
    dist = tmp_path / "dist/publish"
    published_wheel = _make_distribution(dist, "lading-1.0-py3-none-any.whl")
    remaining_wheel = _make_distribution(dist, "lading-1.0-py2.py3-none-any.whl")
    sdist = _make_distribution(dist, "lading-1.0.tar.gz")
    record = install_recording_command(tmp_path, "uv")
    with _IndexServer([(200, _index((published_wheel.name,)))]) as index:
        staged = _run_staging_step(tmp_path, index.root_url)
    assert staged.returncode == 0, staged.stderr

    step = _find_step(
        RELEASE_WORKFLOW, "Publish distributions to PyPI", job="publish-pypi"
    )

    completed = _run_step(
        step,
        tmp_path,
        env={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "COMMAND_RECORD": str(record),
            "PYPI_INDEX_URL": "https://pypi.test",
        },
    )

    assert completed.returncode == 0, completed.stderr
    arguments = json.loads(record.read_text(encoding="utf-8"))
    assert len(arguments) == 7, (
        "uv should receive its five options and two distributions"
    )
    assert arguments[:3] == ["publish", "--trusted-publishing", "always"], (
        "publication should use Trusted Publishing"
    )
    assert arguments[3:5] == ["--check-url", "https://pypi.test/simple/"], (
        "publication should verify the index state"
    )
    assert set(arguments[5:]) == {
        "dist/publish/" + remaining_wheel.name,
        "dist/publish/" + sdist.name,
    }, "uv should receive only the remaining wheel and sdist"


def test_publish_succeeds_without_calling_uv_when_nothing_remains(
    tmp_path: Path,
) -> None:
    """An idempotent PyPI retry with no local files is a successful no-op."""
    dist = tmp_path / "dist/publish"
    existing_wheel = _make_distribution(dist, "lading-1.0-py3-none-any.whl")
    existing_sdist = _make_distribution(dist, "lading-1.0.tar.gz")
    with _IndexServer([
        (200, _index((existing_wheel.name, existing_sdist.name)))
    ]) as index:
        staged = _run_staging_step(tmp_path, index.root_url)
    assert staged.returncode == 0, staged.stderr
    assert not list(dist.iterdir()), "staging should remove every published file"

    record = install_recording_command(tmp_path, "uv")
    step = _find_step(
        RELEASE_WORKFLOW, "Publish distributions to PyPI", job="publish-pypi"
    )

    completed = _run_step(
        step,
        tmp_path,
        env={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "COMMAND_RECORD": str(record),
            "PYPI_INDEX_URL": "https://pypi.test",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert "No unpublished distributions remain" in completed.stdout, (
        "the no-op should explain why no upload ran"
    )
    assert not record.exists(), "uv must not run when there is nothing left to publish"
