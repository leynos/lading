"""End-to-end cover for staged-tree removal when a process is terminated.

A signal handler cannot be verified by describing it. These tests start a real
process, stage a real tree, send it a real signal, and then look at the
filesystem, because the defect in issue #269 was precisely that the cleanup
path everyone assumed was running never ran.
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

# Stages a tree, reports where it is, and then waits to be signalled.
_STAGE_AND_WAIT = """
import sys
import time
from pathlib import Path

sys.path.insert(0, {root!r})

from lading.commands import publish_staging

build_directory = Path({build_directory!r})
build_directory.mkdir(parents=True, exist_ok=True)
staging_root = build_directory / "workspace"
staging_root.mkdir()
(staging_root / "Cargo.toml").write_text("", encoding="utf-8")
publish_staging._ACTIVE_STAGING_ROOTS.add(build_directory)
{install}
print(staging_root, flush=True)
time.sleep(60)
"""


@contextlib.contextmanager
def _staged_process(
    tmp_path: Path, *, install: bool
) -> cabc.Iterator[tuple[subprocess.Popen[str], Path]]:
    """Run a process holding a staged tree, and yield it with that tree.

    Yields
    ------
    tuple[subprocess.Popen[str], Path]
        The running process and the staged directory it owns.
    """
    build_directory = tmp_path / "lading-publish-probe"
    source = _STAGE_AND_WAIT.format(
        root=str(REPOSITORY_ROOT),
        build_directory=str(build_directory),
        install=(
            "publish_staging.install_termination_cleanup()" if install else "pass"
        ),
    )
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
            yield process, build_directory
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
