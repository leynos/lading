"""End-to-end tests for the release wheel upload script.

The release workflow runs this script as a process and reads only its exit
status and output, so these tests do the same: a real subprocess, the recording
stub ``gh`` from :mod:`tests.helpers.gh_stub` on ``PATH``, and assertions on the
exit status and the argv the stub recorded. The unit tests cover the helpers;
these cover the contract the workflow relies on.

These runs use the repository path -- the already-installed interpreter --
because the standalone path has its own scenarios in
``tests/bdd/features/release_wheel_upload.feature``. Both paths go through the
same helper, so neither can reach a real ``gh`` or inherit a credential.
"""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.gh_stub import (
    STUB_TAG,
    GhStub,
    install_gh_stub,
    isolated_environment,
    run_uploader,
)

if typ.TYPE_CHECKING:
    import subprocess

pytestmark = pytest.mark.timeout(60)


@pytest.fixture(name="stub")
def stub_fixture(tmp_path: Path) -> GhStub:
    """Return a stub ``gh`` that succeeds."""
    return install_gh_stub(tmp_path)


def _run(
    stub: GhStub,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the script the way the workflow does and capture its result.

    Parameters
    ----------
    stub : GhStub
        The stub the child must resolve ``gh`` to.
    *arguments : str
        Arguments for the uploader.
    environment : dict[str, str] | None
        Overrides applied to the isolated environment; the tag goes here.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed run.
    """
    child = isolated_environment(stub, extra=environment or {})
    return run_uploader("repository", child, *arguments)


def _make_wheel(directory: Path, name: str) -> Path:
    """Create an empty file named like a wheel and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / name
    wheel.write_bytes(b"")
    return wheel


def test_an_empty_directory_exits_non_zero(tmp_path: Path, stub: GhStub) -> None:
    """The step fails when the build produced no wheel.

    This is the regression: the shell pipeline it replaced exited zero here,
    so the release published with nothing attached and the job stayed green.
    """
    dist = tmp_path / "dist"
    dist.mkdir()

    result = _run(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    assert "No wheel found" in result.stderr, result.stderr
    assert stub.calls() == (), "gh must not be invoked when there is no wheel"


def test_every_wheel_reaches_gh_with_the_tag_from_the_environment(
    tmp_path: Path, stub: GhStub
) -> None:
    """The tag comes from ``GITHUB_REF_NAME`` and every wheel is uploaded once."""
    dist = tmp_path / "dist"
    first = _make_wheel(dist, "a-1.0-py3-none-any.whl")
    second = _make_wheel(dist / "nested", "b-1.0-py3-none-any.whl")

    result = _run(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v9.9.9"},
    )

    assert result.returncode == 0, result.stderr
    calls = stub.calls()
    assert len(calls) == 1, f"expected one call, recorded {calls}"
    expected = ("release", "upload", "v9.9.9", str(first), str(second), "--clobber")
    assert calls[0].argv == expected, f"gh received {calls[0].argv}"


def _error_line(stderr: str) -> str:
    """Return the uploader's own ``Error:`` line from ``stderr``.

    Returns
    -------
    str
        The reported failure line.

    Raises
    ------
    AssertionError
        If the uploader reported no error line.
    """
    for line in stderr.splitlines():
        if line.startswith("Error: "):
            return line
    message = f"no uploader error line in {stderr!r}"
    raise AssertionError(message)


def test_a_failing_gh_fails_the_step(tmp_path: Path) -> None:
    """A rejected upload is a failed release, and keeps gh's own diagnostic.

    The assertion is made on the uploader's own error line rather than on all
    of stderr: the stub writes its diagnostic to inherited stderr too, so a
    search of the whole stream would pass even if the command boundary had
    discarded it.
    """
    diagnostic = "HTTP 422: release asset already exists"
    rejecting = install_gh_stub(tmp_path, exit_code=1, stderr=f"{diagnostic}\n")
    dist = tmp_path / "dist"
    _make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = _run(
        rejecting,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    reported = _error_line(result.stderr)
    assert "gh release upload failed" in reported, reported
    assert diagnostic in reported, reported


def test_a_repository_mode_run_reaches_no_real_release(
    tmp_path: Path, stub: GhStub
) -> None:
    """The repository path gets the same protections as the standalone one.

    The older stub only prepended a directory to ``PATH``. This asserts the
    fuller contract: the child inherited the environment, saw no credential,
    and issued no call that could change a release.
    """
    dist = tmp_path / "dist"
    _make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = _run(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": STUB_TAG},
    )

    assert result.returncode == 0, result.stderr
    calls = stub.calls()
    assert calls, "the uploader never reached gh"
    assert calls[0].sentinel is None, "no sentinel was set for this run"
    assert not calls[0].saw_gh_token, "GH_TOKEN reached the child"
    assert not calls[0].saw_github_token, "GITHUB_TOKEN reached the child"
    assert not [call for call in calls if call.is_release_mutation], calls


def test_a_missing_directory_is_reported_as_such(tmp_path: Path, stub: GhStub) -> None:
    """A download that never ran reads differently from an empty build."""
    result = _run(
        stub,
        "--directory",
        str(tmp_path / "absent"),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    assert "does not exist" in result.stderr, result.stderr


@pytest.mark.parametrize("arguments", [(), ("--directory", "dist")])
def test_the_tag_is_required(
    tmp_path: Path, stub: GhStub, arguments: tuple[str, ...]
) -> None:
    """Without a tag the script refuses rather than guessing one."""
    result = _run(stub, *arguments)

    assert result.returncode != 0, "a missing tag must not upload anything"


def _outcome_line(stderr: str) -> dict[str, object]:
    """Return the decoded ``release_wheel_upload`` summary from ``stderr``.

    Returns
    -------
    dict[str, object]
        The decoded summary object.

    Raises
    ------
    AssertionError
        If the step emitted no summary line.
    """
    prefix = "release_wheel_upload "
    for line in stderr.splitlines():
        if line.startswith(prefix):
            return json.loads(line[len(prefix) :])
    message = f"no outcome line in {stderr!r}"
    raise AssertionError(message)


def test_a_successful_step_reports_its_outcome(tmp_path: Path, stub: GhStub) -> None:
    """A success names itself, counts its wheels, and times both phases."""
    dist = tmp_path / "dist"
    _make_wheel(dist, "a-1.0-py3-none-any.whl")
    output = tmp_path / "github-output"
    output.touch()

    result = _run(
        stub,
        "--directory",
        str(dist),
        environment={
            "GITHUB_REF_NAME": "v1.2.3",
            "GITHUB_OUTPUT": str(output),
        },
    )

    assert result.returncode == 0, result.stderr
    summary = _outcome_line(result.stderr)
    assert summary["outcome"] == "success", summary
    assert summary["wheels"] == 1, summary
    assert {"discovery_seconds", "upload_seconds"} <= set(summary), summary
    written = output.read_text(encoding="utf-8")
    assert "outcome=success" in written, written


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("empty", "no-wheel"),
        ("absent", "missing-directory"),
        ("file", "not-a-directory"),
        ("rejected", "upload-failed"),
    ],
)
def test_each_failure_reports_its_own_outcome(
    tmp_path: Path, scenario: str, expected: str
) -> None:
    """Every way the step can fail reports a distinct, bounded outcome.

    One shared failure label would make the release log unable to tell a build
    that produced nothing from an upload GitHub rejected.
    """
    stub = install_gh_stub(tmp_path, exit_code=1 if scenario == "rejected" else 0)
    directory = _prepare_scenario(tmp_path, scenario)

    result = _run(
        stub,
        "--directory",
        str(directory),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, result.stderr
    summary = _outcome_line(result.stderr)
    assert summary["outcome"] == expected, summary
    assert summary["wheels"] == 0, summary


def _prepare_scenario(tmp_path: Path, scenario: str) -> Path:
    """Build the artefact path for a named failure scenario."""
    directory = tmp_path / "dist"
    match scenario:
        case "empty":
            directory.mkdir()
        case "file":
            directory.write_bytes(b"")
        case "rejected":
            _make_wheel(directory, "a-1.0-py3-none-any.whl")
        case _:
            pass  # "absent": the directory is deliberately never created
    return directory
