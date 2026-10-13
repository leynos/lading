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

import json
from pathlib import Path

import pytest

from tests.e2e.helpers.wheel_upload import (
    make_sdist,
    make_wheel,
    run_repository_upload,
)
from tests.helpers.gh_stub import STUB_TAG, GhStub, GhStubConfig, install_gh_stub

pytestmark = pytest.mark.timeout(60)


def test_an_empty_directory_exits_non_zero(tmp_path: Path, stub: GhStub) -> None:
    """The step fails when the build produced no distribution.

    This is the regression: the shell pipeline it replaced exited zero here,
    so the release published with nothing attached and the job stayed green.
    """
    dist = tmp_path / "dist"
    dist.mkdir()

    result = run_repository_upload(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    assert "No distributions found" in result.stderr, result.stderr
    assert stub.calls() == (), "gh must not run when there is no distribution"


def test_every_wheel_reaches_gh_with_the_tag_from_the_environment(
    tmp_path: Path, stub: GhStub
) -> None:
    """The tag comes from the environment and every distribution is uploaded."""
    dist = tmp_path / "dist"
    first = make_wheel(dist, "a-1.0-py3-none-any.whl")
    second = make_wheel(dist / "nested", "b-1.0-py3-none-any.whl")

    source = make_sdist(dist / "sdist", "a-1.0.tar.gz")
    result = run_repository_upload(
        stub, "--directory", str(dist), environment={"GITHUB_REF_NAME": "v9.9.9"}
    )
    assert result.returncode == 0, result.stderr
    calls = stub.calls()
    assert calls[0].argv == (
        "release",
        "view",
        "v9.9.9",
        "--json",
        "assets",
        "--jq",
        ".assets[].name",
    ), calls[0]
    assert calls[1].argv[:3] == ("release", "upload", "v9.9.9"), (
        "the uploader should attach to the requested release tag"
    )
    assert calls[1].argv[3:] == (str(first), str(second), str(source)), (
        "every wheel and the source distribution should be uploaded"
    )


def test_a_rerun_skips_every_distribution_already_attached(
    tmp_path: Path,
) -> None:
    """A repeated tag succeeds without sending any existing filename again."""
    wheel = "a-1.0-py3-none-any.whl"
    sdist = "a-1.0.tar.gz"
    stub = install_gh_stub(tmp_path, GhStubConfig(release_assets=(wheel, sdist)))
    dist = tmp_path / "dist"
    make_wheel(dist, wheel)
    make_sdist(dist, sdist)

    result = run_repository_upload(
        stub,
        "--directory",
        str(dist),
        environment={"GITHUB_REF_NAME": "v9.9.9"},
    )

    assert result.returncode == 0, result.stderr
    assert "Skipping already-attached distribution" in result.stdout, (
        "the retry should report each asset already attached"
    )
    assert [call.argv[:2] for call in stub.calls()] == [("release", "view")], (
        "an all-attached retry should inspect assets without uploading"
    )


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
    rejecting = install_gh_stub(
        tmp_path,
        GhStubConfig(exit_code=1, stderr=f"{diagnostic}\n"),
    )
    dist = tmp_path / "dist"
    make_wheel(dist, "a-1.0-py3-none-any.whl")
    make_sdist(dist, "a-1.0.tar.gz")

    result = run_repository_upload(
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
    make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = run_repository_upload(
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
    result = run_repository_upload(
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
    """Without a tag the script refuses rather than guessing one.

    The asserted text is Cyclopts's own missing-argument diagnostic, so the
    test cannot pass on an unrelated failure -- a missing ``dist`` directory,
    for instance. Cyclopts writes that diagnostic to stdout while still
    exiting non-zero, which is why the assertion is not made on stderr. The
    environment builder also strips ``GITHUB_REF_NAME``, so the check means
    the same thing under CI as it does locally; before that, a CI run
    inherited the branch name as its tag and only failed for some other
    reason.
    """
    result = run_repository_upload(stub, *arguments)

    assert result.returncode != 0, "a missing tag must not upload anything"
    assert "requires an argument" in result.stdout, result.stdout
    assert "--tag" in result.stdout, result.stdout
    assert stub.calls() == (), "gh must not be invoked without a tag"


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
    make_wheel(dist, "a-1.0-py3-none-any.whl")
    make_sdist(dist, "a-1.0.tar.gz")
    output = tmp_path / "github-output"
    output.touch()

    result = run_repository_upload(
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
    assert summary["distributions"] == 2, summary
    assert {"discovery_seconds", "upload_seconds"} <= set(summary), summary
    written = output.read_text(encoding="utf-8")
    assert "outcome=success" in written, written


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("empty", "no-distributions"),
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
    stub = install_gh_stub(
        tmp_path,
        GhStubConfig(exit_code=1 if scenario == "rejected" else 0),
    )
    directory = _prepare_scenario(tmp_path, scenario)

    result = run_repository_upload(
        stub,
        "--directory",
        str(directory),
        environment={"GITHUB_REF_NAME": "v1.2.3"},
    )

    assert result.returncode == 1, result.stderr
    summary = _outcome_line(result.stderr)
    assert summary["outcome"] == expected, summary
    assert summary["distributions"] == 0, summary


def _prepare_scenario(tmp_path: Path, scenario: str) -> Path:
    """Build the artefact path for a named failure scenario."""
    directory = tmp_path / "dist"
    match scenario:
        case "empty":
            directory.mkdir()
        case "file":
            directory.write_bytes(b"")
        case "rejected":
            make_wheel(directory, "a-1.0-py3-none-any.whl")
        case _:
            pass  # "absent": the directory is deliberately never created
    return directory
