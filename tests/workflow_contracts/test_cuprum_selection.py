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
fails. The freshness test covers the half a version comparison cannot prove:
interesting `uv.lock` facts survive a stale lock, because ``make`` re-locks
silently before the suite runs.
"""

from __future__ import annotations

import collections.abc as cabc
import importlib.metadata
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

#: The lock commands are resolved to a full path rather than left to ``PATH``
#: lookup at exec time.
UV_BINARY = shutil.which("uv") or "uv"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPOSITORY_ROOT / "pyproject.toml"
LOCKFILE = REPOSITORY_ROOT / "uv.lock"
UPLOAD_SCRIPT = REPOSITORY_ROOT / "scripts" / "upload_release_wheels.py"
SCRIPT_LOCKFILE = REPOSITORY_ROOT / "scripts" / "upload_release_wheels.py.lock"

pytestmark = pytest.mark.timeout(60)

#: The leading distribution name of a requirement string, before whichever
#: specifier or separator follows it. Matching the name this way (rather than
#: splitting on ``==``) keeps a requirement that is a range -- the exact defect
#: the alignment test looks for -- from being read as "cuprum is not named".
_NAME_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")

#: A requirement that pins one exact version of ``cuprum`` and nothing more.
#: Extras or a version range or an environment marker would each let the two
#: paths select different artefacts, which is the drift this test exists for.
PIN_PATTERN = re.compile(r"^cuprum==(?P<version>[^\s,;\[\]]+)$")

#: PEP 723's block delimiter. The reference implementation's single regular
#: expression is greedy across adjacent blocks, so the blocks are paired by
#: scanning for the delimiters instead; a script with two blocks must report
#: two, not one merged and unparseable block.
_METADATA_DELIMITER = re.compile(r"(?m)^# /// ?(?P<type>[a-zA-Z0-9-]*)$")


class SelectionError(AssertionError):
    """A requirement site does not name the expected cuprum version.

    It is an assertion rather than a distinct failure type because every cause
    is a defect in the repository's own configuration; the message names the
    site so the fix is obvious.
    """


def _declared_pin(pyproject_text: str) -> str:
    """Return the exact cuprum version ``pyproject.toml`` declares.

    Parameters
    ----------
    pyproject_text : str
        The parsed text of ``pyproject.toml``.

    Returns
    -------
    str
        The version the repository's own requirement pins.

    Raises
    ------
    SelectionError
        If the requirement is missing, or is not a single exact pin.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by the delegated _pin_from
    document = tomllib.loads(pyproject_text)
    requirements = document["project"]["dependencies"]
    return _pin_from(requirements, site="pyproject.toml [project] dependencies")


def _pin_from(requirements: cabc.Sequence[str], *, site: str) -> str:
    """Return the version pinned by the one cuprum requirement in ``requirements``.

    Parameters
    ----------
    requirements : cabc.Sequence[str]
        Requirement strings from one site.
    site : str
        Human-readable name of the site, used in failure messages.

    Returns
    -------
    str
        The pinned version.

    Raises
    ------
    SelectionError
        If the site holds no cuprum requirement, holds more than one, or holds
        one that is not a single exact pin.
    """
    candidates = [item for item in requirements if _names_cuprum(item)]
    if len(candidates) != 1:
        message = f"{site} must name cuprum exactly once, found {candidates}"
        raise SelectionError(message)
    match = PIN_PATTERN.fullmatch(candidates[0].replace(" ", ""))
    if match is None:
        message = (
            f"{site} must pin cuprum exactly as 'cuprum==<version>' with no "
            f"extras, range, or marker, found {candidates[0]!r}"
        )
        raise SelectionError(message)
    return match.group("version")


#: The distribution name the sites below all constrain. Normalized, because
#: PEP 503 treats ``Cuprum``, ``cuprum``, and ``cup-rum`` as one name.
CUP = "cuprum"


def _names_cuprum(requirement: str) -> bool:
    """Whether ``requirement`` constrains the cuprum distribution."""
    match = _NAME_PATTERN.match(requirement.strip())
    return match is not None and match.group("name").lower().replace("_", "-") == CUP


def _lock_pin(lock_text: str, *, site: str) -> str:
    """Return the version a uv lockfile resolves cuprum to.

    Parameters
    ----------
    lock_text : str
        The text of a uv lockfile.
    site : str
        Human-readable name of the lock, used in failure messages.

    Returns
    -------
    str
        The locked version of cuprum.

    Raises
    ------
    SelectionError
        If the lock holds no cuprum package entry, or holds more than one.
    """
    packages = tomllib.loads(lock_text).get("package", [])
    locked = [
        entry["version"]
        for entry in packages
        if entry.get("name") == CUP and "version" in entry
    ]
    if len(locked) != 1:
        message = (
            f"{site} must contain exactly one cuprum package entry, found {locked}"
        )
        raise SelectionError(message)
    return locked[0]


def _lock_specifier(lock_text: str, *, site: str) -> str:
    """Return the pinned version named by the lock's ``requires-dist`` entry.

    A lockfile records the resolved version *and* a copy of the requirement
    that produced it. Checking both catches the lock that was regenerated
    while ``pyproject.toml`` moved on, where the resolved version is current
    but the recorded requirement is stale and would be re-resolved on the next
    update.

    Parameters
    ----------
    lock_text : str
        The text of a uv lockfile.
    site : str
        Human-readable name of the lock, used in failure messages.

    Returns
    -------
    str
        The version the lock's own requirement pins.

    Raises
    ------
    SelectionError
        If the requirement is missing or is not a single exact pin.
    """
    packages = tomllib.loads(lock_text).get("package", [])
    lading = [entry for entry in packages if entry.get("name") == "lading"]
    if len(lading) != 1:
        message = f"{site} must contain exactly one lading package entry"
        raise SelectionError(message)
    entries = lading[0].get("metadata", {}).get("requires-dist", [])
    specifiers = [
        entry["specifier"]
        for entry in entries
        if entry.get("name") == CUP and "specifier" in entry
    ]
    if len(specifiers) != 1:
        message = (
            f"{site} must record exactly one cuprum requirement, found {specifiers}"
        )
        raise SelectionError(message)
    return _pin_from([f"{CUP}{specifiers[0]}"], site=f"{site} requires-dist")


def _metadata_blocks(script_text: str) -> list[str]:
    """Return the bodies of the script's PEP 723 inline metadata blocks.

    Parameters
    ----------
    script_text : str
        The text of a Python script.

    Returns
    -------
    list[str]
        One body per block, with the ``#`` prefixes removed.

    Raises
    ------
    SelectionError
        If a block is opened and never closed.
    """
    blocks: list[str] = []
    body: list[str] | None = None
    for line in script_text.splitlines():
        delimiter = _METADATA_DELIMITER.match(line)
        if delimiter is None:
            if body is not None:
                body.append(line.removeprefix("#").strip())
            continue
        if body is None:
            body = []
        else:
            blocks.append("\n".join(body))
            body = None
    if body is not None:
        message = "a PEP 723 block is opened but never closed"
        raise SelectionError(message)
    return blocks


def _script_requirements(script_text: str) -> list[str]:
    """Return the dependencies the script's PEP 723 block declares.

    Parameters
    ----------
    script_text : str
        The text of the uploader script.

    Returns
    -------
    list[str]
        The declared requirement strings.

    Raises
    ------
    SelectionError
        If the script declares no inline metadata block, or declares more than
        one. The block is what makes the standalone path independent of the
        project, so a script that lost it would silently be resolved from
        ``pyproject.toml`` instead.
    """
    blocks = _metadata_blocks(script_text)
    if len(blocks) != 1:
        message = f"the uploader must declare exactly one PEP 723 block, found {blocks}"
        raise SelectionError(message)
    return list(tomllib.loads(blocks[0]).get("dependencies", []))


def _installed_version() -> str:
    """Return the cuprum version installed in the running interpreter."""
    return importlib.metadata.version(CUP)


@pytest.mark.xfail(
    strict=True,
    raises=SelectionError,
    reason="5.1.4: cuprum beta not yet selected",
)
def test_every_site_declares_the_same_exact_cuprum_pin() -> None:
    """All five requirement sites name one exact version, and it is installed.

    The expected value comes from ``pyproject.toml`` rather than a literal, so
    this test states alignment rather than a particular version: a bump that
    edits four sites out of five fails, and the message names the one that
    lagged.
    """
    expected = _declared_pin(PYPROJECT.read_text(encoding="utf-8"))

    script = _pin_from(
        _script_requirements(UPLOAD_SCRIPT.read_text(encoding="utf-8")),
        site="scripts/upload_release_wheels.py PEP 723 block",
    )
    assert script == expected, (
        f"the uploader's inline metadata pins cuprum {script}, but "
        f"pyproject.toml pins {expected}"
    )

    lock = LOCKFILE.read_text(encoding="utf-8")
    assert _lock_pin(lock, site="uv.lock") == expected, (
        f"uv.lock resolves cuprum to a version other than {expected}"
    )
    assert _lock_specifier(lock, site="uv.lock") == expected, (
        f"uv.lock's recorded requirement disagrees with pyproject.toml ({expected})"
    )

    assert SCRIPT_LOCKFILE.exists(), (
        f"the standalone path needs its own lock at {SCRIPT_LOCKFILE.name}; "
        "run 'uv lock --script scripts/upload_release_wheels.py'"
    )
    script_lock = SCRIPT_LOCKFILE.read_text(encoding="utf-8")
    assert _lock_pin(script_lock, site=SCRIPT_LOCKFILE.name) == expected, (
        f"{SCRIPT_LOCKFILE.name} resolves cuprum to a version other than {expected}"
    )
    assert _lock_specifier(script_lock, site=SCRIPT_LOCKFILE.name) == expected, (
        f"{SCRIPT_LOCKFILE.name}'s recorded requirement disagrees with "
        f"pyproject.toml ({expected})"
    )

    installed = _installed_version()
    assert installed == expected, (
        f"the test interpreter has cuprum {installed}, not {expected}; run 'uv sync'"
    )


# ---------------------------------------------------------------------------
# Self-tests. Each feeds the checkers a document that must be rejected, so a
# checker that silently agreed with everything cannot pass this module.
# ---------------------------------------------------------------------------


def _pyproject_with(dependency: str) -> str:
    """Return a ``pyproject.toml`` body whose only dependency is ``dependency``."""
    return f'[project]\nname = "lading"\ndependencies = [{dependency!r}]\n'


def test_a_metadata_block_pinning_another_version_is_reported() -> None:
    """The script's own pin is compared, not merely parsed."""
    other = _declared_pin(_pyproject_with("cuprum==0.1.0"))

    assert other == "0.1.0", f"the pin was mis-parsed as {other!r}"
    assert other != _declared_pin(_pyproject_with("cuprum==0.2.0b1")), (
        "two different pins must not compare equal"
    )


@pytest.mark.parametrize(
    "requirement",
    ["cuprum>=0.2.0b1", "cuprum==0.2.0b1,<0.3", "cuprum[extra]==0.2.0b1"],
    ids=["range", "bounded", "extras"],
)
def test_a_requirement_that_is_not_an_exact_pin_is_reported(requirement: str) -> None:
    """A range, a bound, or an extra each defeats cross-path agreement."""
    text = _pyproject_with(requirement)

    with pytest.raises(SelectionError):
        _declared_pin(text)


def test_a_requirement_with_a_marker_is_reported() -> None:
    """A marker would let the two paths resolve differently per environment."""
    text = _pyproject_with('cuprum==0.2.0b1; python_version >= "3.13"')

    with pytest.raises(SelectionError):
        _declared_pin(text)


def test_a_script_without_a_metadata_block_is_reported() -> None:
    """No block means no standalone requirement, which must not read as none."""
    with pytest.raises(SelectionError, match="exactly one PEP 723 block"):
        _script_requirements('"""A script with no inline metadata."""\n')


def test_a_script_with_two_metadata_blocks_is_reported() -> None:
    """Two blocks leave the resolved requirement ambiguous."""
    doubled = (
        "# /// script\n"
        '# dependencies = ["cuprum==0.2.0b1"]\n'
        "# ///\n"
        "# /// script\n"
        '# dependencies = ["cuprum==0.1.0"]\n'
        "# ///\n"
    )

    with pytest.raises(SelectionError, match="exactly one PEP 723 block"):
        _script_requirements(doubled)


def test_a_lock_with_two_cuprum_entries_is_reported() -> None:
    """One name resolving twice means the paths could diverge undetected."""
    lock = (
        '[[package]]\nname = "cuprum"\nversion = "0.1.0"\n'
        '[[package]]\nname = "cuprum"\nversion = "0.2.0b1"\n'
    )

    with pytest.raises(SelectionError, match="exactly one cuprum package entry"):
        _lock_pin(lock, site="a doubled lock")


def test_a_lock_entry_without_a_requirement_is_reported() -> None:
    """A lock that recorded no requirement cannot be checked for staleness."""
    lock = '[[package]]\nname = "lading"\nversion = "0.3.1"\n'

    with pytest.raises(SelectionError, match="exactly one cuprum requirement"):
        _lock_specifier(lock, site="a lock with no requirement")


# ---------------------------------------------------------------------------
# Obligation O1b: the locks are fresh, so neither path resolves on the fly.
# ---------------------------------------------------------------------------


def _uv_lock(arguments: cabc.Sequence[str], *, site: str) -> None:
    """Run one ``uv lock`` freshness check and assert that it passed.

    Parameters
    ----------
    arguments : cabc.Sequence[str]
        The lock command's arguments.
    site : str
        Human-readable name of the lock, used in the failure message.

    Raises
    ------
    AssertionError
        If uv reports the lock as stale. uv's own stderr is included, because
        the alternative is an opaque exit status that cannot be told apart
        from a network failure.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by the assert below
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [UV_BINARY, "lock", *arguments, "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        timeout=45,
    )
    assert completed.returncode == 0, (
        f"{site} is stale; run 'uv lock {' '.join(arguments)}'. "
        f"uv said:\n{completed.stdout}{completed.stderr}"
    )


@pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".git").exists(),
    reason="no Git checkout to re-lock against (for example in mutmut's sandbox)",
)
def test_the_project_lock_is_fresh() -> None:
    """``uv.lock`` matches ``pyproject.toml`` as committed."""
    _uv_lock((), site="uv.lock")


@pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".git").exists(),
    reason="no Git checkout to re-lock against (for example in mutmut's sandbox)",
)
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="5.1.4: cuprum beta not yet selected",
)
def test_the_script_lock_is_fresh() -> None:
    """``scripts/upload_release_wheels.py.lock`` matches the script metadata."""
    _uv_lock(
        ("--script", str(UPLOAD_SCRIPT.relative_to(REPOSITORY_ROOT))),
        site=f"{SCRIPT_LOCKFILE.name}",
    )
