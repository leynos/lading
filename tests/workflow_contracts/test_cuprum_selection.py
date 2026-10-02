"""Contract tests for cuprum's version selection across both dependency paths.

Cuprum reaches lading two ways: the repository (``pyproject.toml`` and
``uv.lock``) and the standalone release uploader, which runs through
``uv run --script`` and reads the script's PEP 723 metadata and its own
adjacent lockfile. The two paths resolve independently, so nothing but a test
keeps them on the same cuprum -- and a partial bump is not hypothetical:
Dependabot edits only ``pyproject.toml`` and ``uv.lock``, and its pull
requests auto-merge.

The alignment test reads the expected version from ``pyproject.toml`` and
contains no version literal of its own, so a bump moves every site together or
fails. The freshness tests cover the half a version comparison cannot prove:
a lock can name the right version and still be stale, so its recorded
requirement is checked too. They read the Git index rather than the working
tree, because ``make build`` and the standalone BDD scenario both re-lock
silently before the suite runs; see the note above them.

The readers those assertions rest on live in
:mod:`tests.helpers.cuprum_selection`, and the documents each must reject are
tested in ``test_cuprum_selection_readers.py``. This module owns what the
repository must satisfy; that one owns whether the reading is correct.
"""

import collections.abc as cabc
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.cuprum_pin import declared_pin, pin_from
from tests.helpers.cuprum_selection import (
    installed_version,
    lock_pin,
    lock_specifier,
    script_requirements,
)

#: The lock commands are resolved to a full path rather than left to ``PATH``
#: lookup at exec time.
UV_BINARY = shutil.which("uv") or "uv"
GIT_BINARY = shutil.which("git") or "git"

#: The two committed-state freshness checks both need the index, and both are
#: skipped for the same reason when it is absent.
_NO_GIT_CHECKOUT = (
    "no Git checkout to read indexed files from (for example in mutmut's sandbox)"
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPOSITORY_ROOT / "pyproject.toml"
LOCKFILE = REPOSITORY_ROOT / "uv.lock"
UPLOAD_SCRIPT = REPOSITORY_ROOT / "scripts" / "upload_release_wheels.py"
SCRIPT_LOCKFILE = REPOSITORY_ROOT / "scripts" / "upload_release_wheels.py.lock"

pytestmark = pytest.mark.timeout(60)


def test_every_site_declares_the_same_exact_cuprum_pin() -> None:
    """All five requirement sites name one exact version, and it is installed.

    The expected value comes from ``pyproject.toml`` rather than a literal, so
    this test states alignment rather than a particular version: a bump that
    edits four sites out of five fails, and the message names the one that
    lagged.
    """
    expected = declared_pin(PYPROJECT.read_text(encoding="utf-8"))

    script = pin_from(
        script_requirements(UPLOAD_SCRIPT.read_text(encoding="utf-8")),
        site="scripts/upload_release_wheels.py PEP 723 block",
    )
    assert script == expected, (
        f"the uploader's inline metadata pins cuprum {script}, but "
        f"pyproject.toml pins {expected}"
    )

    lock = LOCKFILE.read_text(encoding="utf-8")
    assert lock_pin(lock, site="uv.lock") == expected, (
        f"uv.lock resolves cuprum to a version other than {expected}"
    )
    assert lock_specifier(lock, site="uv.lock", origin="project") == expected, (
        f"uv.lock's recorded requirement disagrees with pyproject.toml ({expected})"
    )

    assert SCRIPT_LOCKFILE.exists(), (
        f"the standalone path needs its own lock at {SCRIPT_LOCKFILE.name}; "
        "run 'uv lock --script scripts/upload_release_wheels.py'"
    )
    script_lock = SCRIPT_LOCKFILE.read_text(encoding="utf-8")
    assert lock_pin(script_lock, site=SCRIPT_LOCKFILE.name) == expected, (
        f"{SCRIPT_LOCKFILE.name} resolves cuprum to a version other than {expected}"
    )
    assert (
        lock_specifier(script_lock, site=SCRIPT_LOCKFILE.name, origin="script")
        == expected
    ), (
        f"{SCRIPT_LOCKFILE.name}'s recorded requirement disagrees with "
        f"pyproject.toml ({expected})"
    )

    installed = installed_version()
    assert installed == expected, (
        f"the test interpreter has cuprum {installed}, not {expected}; run 'uv sync'"
    )


# ---------------------------------------------------------------------------
# Obligation O1b: the locks are fresh, so neither path resolves on the fly.
#
# These two tests read the *committed* blobs rather than the working tree, and
# that is the whole point of them. Every other test in this module may read the
# tree, because `make build` (`uv sync`) and the standalone BDD scenario
# (`uv run --script`) both re-lock silently when a lock is stale. A freshness
# check that read the tree would therefore be asserting a condition the gate
# repairs before the assertion runs: it could never fail under `make test`, and
# a stale lock committed to the repository would ship green. Reconstructing the
# pair from `git show` in a scratch directory is what makes the check able to
# fail at all.
# ---------------------------------------------------------------------------


def _committed(path: Path) -> str:
    """Return the committed content of one tracked file.

    Parameters
    ----------
    path : Path
        The tracked file, expressed relative to the repository root.

    Returns
    -------
    str
        The file's content as of the index.

    Raises
    ------
    AssertionError
        If Git cannot produce the blob, so that an environment problem is
        reported as such rather than as a stale lock.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by the assert below
    relative = path.relative_to(REPOSITORY_ROOT).as_posix()
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [GIT_BINARY, "show", f":{relative}"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        timeout=45,
    )
    assert completed.returncode == 0, (
        f"could not read the committed {relative} from the index; git said:\n"
        f"{completed.stderr}"
    )
    return completed.stdout


def _uv_lock_in(
    scratch: Path, arguments: cabc.Sequence[str], *, site: str, cwd: Path
) -> None:
    """Run one ``uv lock`` freshness check against ``scratch`` and assert it passed.

    Parameters
    ----------
    scratch : Path
        The directory holding the files to check. Its content is what uv
        resolves against, so a caller that wants the committed state must
        populate it from the index rather than copying the working tree.
    arguments : cabc.Sequence[str]
        The lock command's arguments, after ``lock``.
    site : str
        Human-readable name of the lock, used in the failure message.
    cwd : Path
        The directory to run uv from. This is the repository for the project
        lock, because ``--script`` takes a path relative to the script's own
        directory rather than resolving the project.

    Raises
    ------
    AssertionError
        If uv reports the lock as stale. uv's own stderr is included, because
        the alternative is an opaque exit status that cannot be told apart
        from a network failure or a missing file.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by the assert below
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [UV_BINARY, "lock", *arguments, "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        timeout=45,
    )
    assert completed.returncode == 0, (
        f"{site} is stale as committed; run 'uv lock {' '.join(arguments)}'. "
        f"uv said:\n{completed.stdout}{completed.stderr}"
    )


@pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".git").exists(),
    reason=_NO_GIT_CHECKOUT,
)
def test_the_project_lock_is_fresh(tmp_path: Path) -> None:
    """``uv.lock`` matches ``pyproject.toml`` as committed, not as built.

    The pair is written into a scratch directory and resolved there. That is
    what keeps this test from being vacuous: `make build` runs `uv sync`, which
    rewrites a stale ``uv.lock`` in place, so a check that read the working
    tree would be asserting a condition the gate had already repaired.
    """
    (tmp_path / PYPROJECT.name).write_text(_committed(PYPROJECT), encoding="utf-8")
    (tmp_path / LOCKFILE.name).write_text(_committed(LOCKFILE), encoding="utf-8")
    _uv_lock_in(tmp_path, (), site="uv.lock", cwd=tmp_path)


@pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".git").exists(),
    reason=_NO_GIT_CHECKOUT,
)
def test_the_script_lock_is_fresh(tmp_path: Path) -> None:
    """``scripts/upload_release_wheels.py.lock`` matches the script metadata.

    The same reconstruction as the project check, and for the same reason: the
    standalone BDD scenario runs ``uv run --script``, which re-locks a stale
    script lock. The script and its lock are written into a scratch directory
    under their committed names, because ``uv lock --script`` looks for
    ``<script>.lock`` beside the script.
    """
    (tmp_path / UPLOAD_SCRIPT.name).write_text(
        _committed(UPLOAD_SCRIPT), encoding="utf-8"
    )
    (tmp_path / SCRIPT_LOCKFILE.name).write_text(
        _committed(SCRIPT_LOCKFILE), encoding="utf-8"
    )
    _uv_lock_in(
        tmp_path,
        ("--script", UPLOAD_SCRIPT.name),
        site=f"{SCRIPT_LOCKFILE.name}",
        cwd=tmp_path,
    )
