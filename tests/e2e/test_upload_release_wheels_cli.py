"""End-to-end tests for the release wheel upload script.

The release workflow runs this script as a process and reads only its exit
status and output, so these tests do the same: a real subprocess, a stub ``gh``
on ``PATH``, and assertions on the exit status and the argv the stub recorded.
The unit tests cover the helpers; these cover the contract the workflow relies
on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "upload_release_wheels.py"

_GH_STUB = """#!{python}
import json
import pathlib
import sys

record = pathlib.Path({record!r})
record.write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
sys.stderr.write({stderr!r})
sys.exit({exit_code})
"""


def _install_gh_stub(directory: Path, *, exit_code: int = 0, stderr: str = "") -> Path:
    """Put a recording ``gh`` on ``PATH`` and return its record file."""
    bin_directory = directory / "bin"
    bin_directory.mkdir(parents=True, exist_ok=True)
    record = directory / "gh-argv.json"
    stub = bin_directory / "gh"
    stub.write_text(
        _GH_STUB.format(
            python=sys.executable,
            record=str(record),
            exit_code=exit_code,
            stderr=stderr,
        ),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return record


def _run(
    directory: Path,
    *arguments: str,
    tag: str | None = None,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the script as the workflow does and capture its result."""
    environment = dict(os.environ)
    environment["PATH"] = f"{directory / 'bin'}{os.pathsep}{environment['PATH']}"
    if tag is None:
        environment.pop("GITHUB_REF_NAME", None)
    else:
        environment["GITHUB_REF_NAME"] = tag
    if github_output is None:
        environment.pop("GITHUB_OUTPUT", None)
    else:
        environment["GITHUB_OUTPUT"] = str(github_output)
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=REPOSITORY_ROOT,
    )


def _make_wheel(directory: Path, name: str) -> Path:
    """Create an empty file named like a wheel and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / name
    wheel.write_bytes(b"")
    return wheel


def test_an_empty_directory_exits_non_zero(tmp_path: Path) -> None:
    """The step fails when the build produced no wheel.

    This is the regression: the shell pipeline it replaced exited zero here,
    so the release published with nothing attached and the job stayed green.
    """
    record = _install_gh_stub(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()

    result = _run(tmp_path, "--directory", str(dist), tag="v1.2.3")

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    assert "No wheel found" in result.stderr, result.stderr
    assert not record.exists(), "gh must not be invoked when there is no wheel"


def test_every_wheel_reaches_gh_with_the_tag_from_the_environment(
    tmp_path: Path,
) -> None:
    """The tag comes from ``GITHUB_REF_NAME`` and every wheel is uploaded once."""
    record = _install_gh_stub(tmp_path)
    dist = tmp_path / "dist"
    first = _make_wheel(dist, "a-1.0-py3-none-any.whl")
    second = _make_wheel(dist / "nested", "b-1.0-py3-none-any.whl")

    result = _run(tmp_path, "--directory", str(dist), tag="v9.9.9")

    assert result.returncode == 0, result.stderr
    recorded = json.loads(record.read_text(encoding="utf-8"))
    expected = ["release", "upload", "v9.9.9", str(first), str(second), "--clobber"]
    assert recorded == expected, f"gh received {recorded}"


def test_a_failing_gh_fails_the_step(tmp_path: Path) -> None:
    """A rejected upload is a failed release, and keeps gh's own diagnostic.

    Asserting only the uploader's own wording would pass even if the command
    boundary discarded gh's stderr, which is the part that says why.
    """
    diagnostic = "HTTP 422: release asset already exists"
    _install_gh_stub(tmp_path, exit_code=1, stderr=diagnostic)
    dist = tmp_path / "dist"
    _make_wheel(dist, "a-1.0-py3-none-any.whl")

    result = _run(tmp_path, "--directory", str(dist), tag="v1.2.3")

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    assert "gh release upload failed" in result.stderr, result.stderr
    assert diagnostic in result.stderr, result.stderr


def test_a_missing_directory_is_reported_as_such(tmp_path: Path) -> None:
    """A download that never ran reads differently from an empty build."""
    _install_gh_stub(tmp_path)

    result = _run(tmp_path, "--directory", str(tmp_path / "absent"), tag="v1.2.3")

    assert result.returncode == 1, f"expected failure, got {result.returncode}"
    assert "does not exist" in result.stderr, result.stderr


@pytest.mark.parametrize("arguments", [(), ("--directory", "dist")])
def test_the_tag_is_required(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    """Without a tag the script refuses rather than guessing one."""
    _install_gh_stub(tmp_path)

    result = _run(tmp_path, *arguments, tag=None)

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


def test_a_successful_step_reports_its_outcome(tmp_path: Path) -> None:
    """A success names itself, counts its wheels, and times both phases."""
    _install_gh_stub(tmp_path)
    dist = tmp_path / "dist"
    _make_wheel(dist, "a-1.0-py3-none-any.whl")
    output = tmp_path / "github-output"
    output.touch()

    result = _run(
        tmp_path, "--directory", str(dist), tag="v1.2.3", github_output=output
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
    _install_gh_stub(tmp_path, exit_code=1 if scenario == "rejected" else 0)
    directory = _prepare_scenario(tmp_path, scenario)

    result = _run(tmp_path, "--directory", str(directory), tag="v1.2.3")

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
