"""Read the cuprum version each dependency path resolves to.

Both paths record their requirement in a lockfile, but not in the same place.
A ``uv lock --script`` lock resolves the script's dependencies alone, so it
holds no package entry for the script at all and its requirement sits in a
top-level manifest; a project lock resolves the project itself, so the
requirement is in the ``lading`` package's metadata. Reading either with the
other's rule reports a correct lock as broken, which is why the shape is a
parameter rather than something guessed.

The readers live here rather than in the contract test for the reason
:mod:`tests.helpers.cuprum_pin` gives: two modules assert on them, so neither
should become the other's library. Their self-tests -- the documents each
reader must reject -- are in ``test_cuprum_selection_readers.py``, kept apart
from the alignment and freshness assertions that use them so that a broken
reader is reported against the reader rather than against whichever assertion
it happened to break.
"""

import importlib.metadata
import re
import tomllib
import typing as typ

from tests.helpers.cuprum_pin import CUP, SelectionError, pin_from

#: PEP 723's block delimiter. The reference implementation's single regular
#: expression is greedy across adjacent blocks, so the blocks are paired by
#: scanning for the delimiters instead; a script with two blocks must report
#: two, not one merged and unparseable block.
_METADATA_DELIMITER = re.compile(r"(?m)^# /// ?(?P<type>[a-zA-Z0-9-]*)$")


def lock_pin(lock_text: str, *, site: str) -> str:
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


#: Which kind of uv lock a check is reading. The two are not interchangeable:
#: a project lock resolves the project itself, so its requirement lives in the
#: ``lading`` package's metadata, while a ``--script`` lock resolves only the
#: script's dependencies and has no package for the script at all -- its
#: requirement is in a top-level manifest. Naming the shape at the call site,
#: rather than trying one and falling back to the other, keeps a script lock
#: that lost its manifest from reading as a lock that merely names no cuprum.
type LockOrigin = typ.Literal["project", "script"]


def project_lock_requirements(
    document: dict[str, typ.Any], *, site: str
) -> list[dict[str, typ.Any]]:
    """Return the requirements a project lock records for ``lading`` itself.

    A project lock resolves the project, so the requirement being checked sits
    in the ``lading`` package's own metadata rather than in a manifest.

    Parameters
    ----------
    document : dict[str, typ.Any]
        The parsed lockfile.
    site : str
        Human-readable name of the lock, used in failure messages.

    Returns
    -------
    list[dict[str, typ.Any]]
        The requirement entries recorded against the ``lading`` package.

    Raises
    ------
    SelectionError
        If the lock holds no ``lading`` package entry, or holds more than one.
    """
    lading = [
        entry for entry in document.get("package", []) if entry.get("name") == "lading"
    ]
    if len(lading) != 1:
        message = f"{site} must contain exactly one lading package entry"
        raise SelectionError(message)
    return lading[0].get("metadata", {}).get("requires-dist", [])


def script_lock_requirements(
    document: dict[str, typ.Any],
) -> list[dict[str, typ.Any]]:
    """Return the requirements a script lock records in its top-level manifest.

    A ``uv lock --script`` lock resolves the script's dependencies alone, so it
    holds no package entry for the script at all; its requirement sits in the
    manifest instead.

    Parameters
    ----------
    document : dict[str, typ.Any]
        The parsed lockfile.

    Returns
    -------
    list[dict[str, typ.Any]]
        The requirement entries recorded in the manifest.
    """
    return document.get("manifest", {}).get("requirements", [])


def lock_specifier(lock_text: str, *, site: str, origin: LockOrigin) -> str:
    """Return the pinned version named by the lock's own recorded requirement.

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
    origin : LockOrigin
        Which shape of lock this is: ``"project"`` or ``"script"``.

    Returns
    -------
    str
        The version the lock's own requirement pins.

    Raises
    ------
    SelectionError
        If the requirement is missing or is not a single exact pin.
    """
    document = tomllib.loads(lock_text)
    entries = (
        project_lock_requirements(document, site=site)
        if origin == "project"
        else script_lock_requirements(document)
    )
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
    return pin_from([f"{CUP}{specifiers[0]}"], site=f"{site} requirement")


def metadata_blocks(script_text: str) -> list[str]:
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


def script_requirements(script_text: str) -> list[str]:
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
    blocks = metadata_blocks(script_text)
    if len(blocks) != 1:
        message = f"the uploader must declare exactly one PEP 723 block, found {blocks}"
        raise SelectionError(message)
    return list(tomllib.loads(blocks[0]).get("dependencies", []))


def installed_version() -> str:
    """Return the cuprum version installed in the running interpreter."""
    return importlib.metadata.version(CUP)
