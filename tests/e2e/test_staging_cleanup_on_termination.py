"""End-to-end cover for staged-tree removal when a process is terminated.

A signal handler cannot be verified by describing it. These tests start a real
process, drive lading's own staging, send it a real signal, and then look at
the filesystem, because the defect in issue #269 was precisely that the
cleanup path everyone assumed was running never ran.

The probe enters :func:`publish_staging.staged_workspace` rather than
registering a path by hand, so the order lading itself uses is what is under
test. The copy is held open by a stand-in for :func:`shutil.copytree`, which
is the only way to make the window deterministic: the real copy of a real
workspace finishes in milliseconds here and in minutes in production, and it
is the production shape the signal has to survive.
"""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Stages a real workspace through lading, reports where it went, and then
# waits to be signalled. `when` decides whether the signal arrives while the
# copy is still running or after the staged tree is complete.
_STAGE_AND_WAIT = """
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, {root!r})

from lading.commands import publish_staging
from lading.commands.publish import PublishOptions
from lading.commands.publish_plan import PublishPlan

workspace_root = Path({workspace_root!r})
build_directory = Path({build_directory!r})
plan = PublishPlan(
    workspace_root=workspace_root,
    publishable=(),
    skipped_manifest=(),
    skipped_configuration=(),
)
options = PublishOptions(build_directory=build_directory, cleanup=True)
{install}

if {during_copy!r}:
    real_copytree = shutil.copytree

    def slow_copytree(*arguments, **keywords):
        '''Start the copy, then hold it open until the signal arrives.'''
        result = real_copytree(*arguments, **keywords)
        print(build_directory, flush=True)
        time.sleep(60)
        return result

    shutil.copytree = slow_copytree

with publish_staging.staged_workspace(plan, options=options) as preparation:
    print(preparation.staging_root, flush=True)
    time.sleep(60)
"""


def _workspace(tmp_path: Path) -> Path:
    """Create the smallest tree lading will agree to stage.

    Returns
    -------
    Path
        The workspace root the probe copies.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "Cargo.toml").write_text(
        "[workspace]\nmembers = []\n", encoding="utf-8"
    )
    return workspace_root


@contextlib.contextmanager
def _staged_process(
    tmp_path: Path, *, install: bool, during_copy: bool = False
) -> cabc.Iterator[tuple[subprocess.Popen[str], Path]]:
    """Run a process holding a staged tree, and yield it with that tree.

    Parameters
    ----------
    tmp_path : Path
        Directory to build the workspace and its staging area under.
    install : bool
        Whether the probe installs the termination handler.
    during_copy : bool
        Whether the signal should arrive while the copy is still running,
        rather than after the staged tree is complete.

    Yields
    ------
    tuple[subprocess.Popen[str], Path]
        The running process and the staged directory it owns.
    """
    build_directory = tmp_path / "lading-publish-probe"
    source = _STAGE_AND_WAIT.format(
        root=str(REPOSITORY_ROOT),
        workspace_root=str(_workspace(tmp_path)),
        build_directory=str(build_directory),
        during_copy=during_copy,
        install=(
            "publish_staging.install_termination_cleanup()" if install else "pass"
        ),
    )
    # The build directory is supplied, so it belongs to the caller and only
    # the copy inside it is lading's to remove. That copy is what these tests
    # watch.
    staged_copy = build_directory / "workspace"
    with subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPOSITORY_ROOT,
    ) as process:
        try:
            if process.stdout is not None:
                process.stdout.readline()
            yield process, staged_copy
        finally:
            if process.poll() is None:  # pragma: no cover - defensive
                process.kill()


def _terminate(process: subprocess.Popen[str]) -> int:
    """Send SIGTERM and return the exit status, killing it if it lingers.

    Returns
    -------
    int
        The process's exit status.
    """
    process.send_signal(signal.SIGTERM)
    try:
        return process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()
        return process.wait(timeout=10)


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX-only")
def test_sigterm_removes_the_staged_tree(tmp_path: Path) -> None:
    """A terminated publish does not leave its staged copy behind.

    This is the case the previous design could not cover: neither an
    ``atexit`` hook nor a ``finally`` block runs when a process is terminated.
    """
    with _staged_process(tmp_path, install=True) as (process, build_directory):
        assert build_directory.is_dir(), "the probe did not stage anything"

        _terminate(process)

        assert not build_directory.exists(), f"{build_directory} survived SIGTERM"


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX-only")
def test_sigterm_during_the_copy_removes_the_partial_tree(tmp_path: Path) -> None:
    """The copy is the longest window, so it is the one that must be covered.

    Copying a workspace takes minutes in production. A design that registered
    the cleanup target only once the copy returned would leave the partial
    tree behind for exactly the interruption issue #269 describes, while every
    test that signalled a completed tree still passed.
    """
    with _staged_process(tmp_path, install=True, during_copy=True) as (
        process,
        staged_copy,
    ):
        assert staged_copy.is_dir(), "the probe was not copying yet"

        _terminate(process)

        assert not staged_copy.exists(), (
            f"{staged_copy} survived a SIGTERM sent during the copy"
        )


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX-only")
def test_the_tree_survives_without_the_handler(tmp_path: Path) -> None:
    """Without the handler installed the tree is left behind.

    The companion to the test above: it shows the first one is measuring the
    handler rather than something the interpreter would have done anyway.
    """
    with _staged_process(tmp_path, install=False) as (process, build_directory):
        assert build_directory.is_dir(), "the probe did not stage anything"

        _terminate(process)

        assert build_directory.exists(), "this case is what the handler exists to fix"


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX-only")
def test_termination_still_ends_the_process(tmp_path: Path) -> None:
    """Cleaning up must not swallow the signal.

    A handler that returns normally would leave the process running, turning a
    termination request into a hang.
    """
    with _staged_process(tmp_path, install=True) as (process, _):
        started = time.monotonic()
        status = _terminate(process)

        assert time.monotonic() - started < 10, "the process did not end promptly"
        assert status == -signal.SIGTERM, f"expected death by SIGTERM, got {status}"
