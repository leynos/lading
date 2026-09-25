"""Tests for the recording ``gh`` stub and its environment isolation.

The stub is what makes every other upload test safe: it is the ``gh`` a child
process finds, so a test can never attach an asset to a real release. That
guarantee is worth nothing if the helper itself is wrong, so the guarantees
are asserted here directly rather than inferred from the tests that use them.

The load-bearing assertion is :func:`test_the_token_is_removed_not_merely_absent`.
A helper that never set a token would pass a check for the token's absence
without protecting anything, so the parent is given one first.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.gh_stub import (
    SENTINEL_VARIABLE,
    STUB_TAG,
    GhStub,
    install_gh_stub,
    isolated_environment,
    run_uploader,
)

pytestmark = pytest.mark.timeout(60)

#: A tag that cannot exist on GitHub, so even a catastrophic failure to stub
#: would find nothing to modify.
_IMPOSSIBLE_TAG = "v0.0.0-stub"


@pytest.fixture(name="stub")
def stub_fixture(tmp_path: Path) -> GhStub:
    """Return a stub installed under a temporary directory."""
    return install_gh_stub(tmp_path)


def test_the_installed_stub_is_executable(stub: GhStub) -> None:
    """The stub can be executed, which ``PATH`` resolution alone does not prove."""
    executable = stub.bin_directory / "gh"
    mode = executable.stat().st_mode
    assert mode & stat.S_IXUSR, f"{executable} is not executable by its owner"


def test_the_guard_rejects_a_caller_override_that_hides_the_stub(
    stub: GhStub,
) -> None:
    """A final environment that resolves ``gh`` elsewhere is refused.

    The guard checks the environment as built rather than trusting the order
    the helper assembles it in. ``extra`` is applied last, so a caller passing
    its own ``PATH`` would drop the stub off the front and restore the real
    binary -- the one mistake that could reach a live release. The ambient
    ``PATH`` cannot cause this (the stub is prepended to it), so the override
    is the reachable hazard, and the assertion is what makes it unreachable.
    """
    shadow = stub.bin_directory.parent / "shadow"
    shadow.mkdir()
    (shadow / "gh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="must be the gh on PATH"):
        isolated_environment(stub, extra={"PATH": str(shadow)})


def test_the_guard_passes_when_the_stub_is_first(stub: GhStub) -> None:
    """The ordinary case is accepted, so the guard is not merely always-failing."""
    environment = isolated_environment(stub)

    assert environment["PATH"].split(os.pathsep)[0] == str(stub.bin_directory)


def test_the_token_is_removed_not_merely_absent(
    stub: GhStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token set in the parent does not reach the child, which proves removal.

    The parent is given a real-looking token first. If the helper merely never
    exported one, this test would still pass -- but it would pass for a helper
    that inherited the whole environment, which is the defect it exists to
    catch. Setting the variable makes the two behaviours distinguishable.
    """
    monkeypatch.setenv("GH_TOKEN", "ghp_parent_token_must_not_be_inherited")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_parent_token_must_not_be_inherited")

    environment = isolated_environment(stub)

    assert "GH_TOKEN" not in environment, "GH_TOKEN survived into the child"
    assert "GITHUB_TOKEN" not in environment, "GITHUB_TOKEN survived into the child"


def test_the_sentinel_is_inherited(stub: GhStub) -> None:
    """The environment is inherited, which production needs for its token.

    This is the other half of the token assertion: the helper strips two named
    variables and passes the rest through, rather than clearing the
    environment. A release that ran with no environment at all would fail for
    a reason unrelated to the upload.
    """
    environment = isolated_environment(stub, sentinel="carried-through")

    assert environment[SENTINEL_VARIABLE] == "carried-through"


def test_the_configuration_directory_is_empty(stub: GhStub) -> None:
    """``GH_CONFIG_DIR`` points at an empty directory, not the machine's login."""
    environment = isolated_environment(stub)

    config = Path(environment["GH_CONFIG_DIR"])
    assert config.is_dir(), f"{config} is not a directory"
    assert not list(config.iterdir()), f"{config} is not empty"


def test_the_host_is_invalid_and_prompts_are_disabled(stub: GhStub) -> None:
    """A mistaken call cannot reach GitHub, and cannot block waiting for input."""
    environment = isolated_environment(stub)

    assert environment["GH_HOST"] == "stub.invalid"
    assert environment["GH_PROMPT_DISABLED"] == "1"


def test_extra_variables_are_applied_last(stub: GhStub) -> None:
    """A caller's overrides win, so one helper serves every scenario."""
    environment = isolated_environment(stub, extra={"GITHUB_REF_NAME": STUB_TAG})

    assert environment["GITHUB_REF_NAME"] == STUB_TAG


def test_the_project_variables_are_stripped(stub: GhStub) -> None:
    """Uv's project variables are removed, so the standalone path is standalone.

    ``uv run --script`` must build the child environment from the script's own
    metadata. A leaked ``UV_PROJECT`` or ``VIRTUAL_ENV`` would point it at the
    repository instead, and the run would prove nothing about the script's
    lockfile.
    """
    environment = isolated_environment(stub)

    for variable in ("UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIRECTORY"):
        assert variable not in environment, f"{variable} survived into the child"


def test_the_stub_records_every_call(stub: GhStub) -> None:
    """Calls append, so a second invocation is visible and countable.

    The stub this replaced overwrote its record, which meant a test asserting
    "called once" could never fail -- a stray second upload was invisible.
    """
    executable = stub.bin_directory / "gh"
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [sys.executable, str(executable), "release", "view"],
        check=False,
        env=isolated_environment(stub),
    )
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [sys.executable, str(executable), "release", "upload"],
        check=False,
        env=isolated_environment(stub),
    )

    calls = stub.calls()
    assert len(calls) == 2, f"expected two recorded calls, got {calls}"
    assert calls[0].argv == ("release", "view"), calls[0]
    assert calls[1].argv == ("release", "upload"), calls[1]


def test_an_empty_record_reports_no_calls(tmp_path: Path) -> None:
    """A stub that was never invoked reports an empty tuple, not an error."""
    stub = install_gh_stub(tmp_path)

    assert stub.calls() == ()


def test_the_stub_can_fail_with_a_chosen_diagnostic(tmp_path: Path) -> None:
    """The exit status and stderr are settable, which the failure tests rely on."""
    stub = install_gh_stub(tmp_path, exit_code=1, stderr="HTTP 422: asset exists\n")

    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [sys.executable, str(stub.bin_directory / "gh"), "release", "upload"],
        capture_output=True,
        text=True,
        check=False,
        env=isolated_environment(stub),
    )

    assert completed.returncode == 1, completed
    assert completed.stderr == "HTTP 422: asset exists\n", completed.stderr


def test_the_record_is_one_json_line_per_call(stub: GhStub) -> None:
    """The record stays parseable line-by-line, which ``calls()`` depends on."""
    executable = stub.bin_directory / "gh"
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [sys.executable, str(executable), "release", "view"],
        check=False,
        env=isolated_environment(stub),
    )

    lines = stub.record.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, lines
    entry = json.loads(lines[0])
    assert entry["argv"] == ["release", "view"], entry


def test_the_release_mutation_predicate_recognises_both_calls(stub: GhStub) -> None:
    """Only ``release create`` and ``release edit`` count as mutating a release."""
    executable = stub.bin_directory / "gh"
    for arguments in (
        ("release", "view"),
        ("release", "create", "v0.0.0-stub"),
        ("release", "edit", "v0.0.0-stub"),
        ("release", "upload", "v0.0.0-stub"),
    ):
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            [sys.executable, str(executable), *arguments],
            check=False,
            env=isolated_environment(stub),
        )

    mutations = [call.argv for call in stub.calls() if call.is_release_mutation]

    assert mutations == [
        ("release", "create", "v0.0.0-stub"),
        ("release", "edit", "v0.0.0-stub"),
    ], mutations


def test_the_uploader_runs_the_script_through_the_chosen_interpreter(
    stub: GhStub, tmp_path: Path
) -> None:
    """The repository mode runs the script directly, without uv resolving it.

    The two modes are the two dependency paths, so the mode has to select the
    command: a repository-mode run that went through ``uv run --script`` would
    exercise the standalone lockfile while claiming to test the project's.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "lading-0.0.0-py3-none-any.whl").write_bytes(b"")
    environment = isolated_environment(stub, extra={"GITHUB_REF_NAME": _IMPOSSIBLE_TAG})

    completed = run_uploader("repository", environment, "--directory", str(dist))

    assert completed.returncode == 0, completed.stderr
    calls = stub.calls()
    assert len(calls) == 1, calls
    assert calls[0].argv[:3] == ("release", "upload", _IMPOSSIBLE_TAG), calls[0]
