"""Read the cuprum version a requirement site declares.

Two suites need the same reading. The selection contract test compares every
requirement site and both lockfiles, and the standalone BDD scenario compares
the script lock's cuprum against ``pyproject.toml``. Keeping the readers here
rather than in the contract test stops one test module from being a library for
another: a rename, or a move under a different collection rule, would otherwise
break the BDD suite for no reason.
"""

from __future__ import annotations

import collections.abc as cabc
import re
import tomllib

#: The distribution name the sites all constrain. Only case varies among the
#: spellings that normalise to this: ``Cuprum`` and ``CUPRUM`` are this name,
#: while ``cup-rum`` is a different one.
CUP = "cuprum"

#: The leading distribution name of a requirement string, before whichever
#: specifier or separator follows it. Matching the name this way (rather than
#: splitting on ``==``) keeps a requirement that is a range -- the exact defect
#: the alignment test looks for -- from being read as "cuprum is not named".
_NAME_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")

#: A requirement that pins one exact version of ``cuprum`` and nothing more.
#: Extras or a version range or an environment marker would each let the two
#: paths select different artefacts, which is the drift the caller looks for.
PIN_PATTERN = re.compile(r"^cuprum==(?P<version>[^\s,;\[\]]+)$")


class SelectionError(AssertionError):
    """A requirement site does not name the expected cuprum version.

    It is an assertion rather than a distinct failure type because every cause
    is a defect in the repository's own configuration; the message names the
    site so the fix is obvious.
    """


def names_cuprum(requirement: str) -> bool:
    """Whether ``requirement`` constrains the cuprum distribution.

    The name is normalised as PEP 503 specifies -- runs of ``-``, ``_``, and
    ``.`` collapse to a single ``-``, then the result is lowercased -- so this
    agrees with the index about which spellings denote the same project. For
    this target only the case-insensitivity is reachable, because ``cuprum``
    holds no separator for the collapse rule to act on, and a spelling that
    introduces one normalises to ``cup-rum``, which is a different project.
    ``test_the_name_matcher_agrees_with_pep_503_on_what_is_cuprum`` pins both
    halves of that.

    Parameters
    ----------
    requirement : str
        One requirement string from a site.

    Returns
    -------
    bool
        Whether the requirement's distribution name normalises to ``cuprum``.
    """
    match = _NAME_PATTERN.match(requirement.strip())
    if match is None:
        return False
    return re.sub(r"[-_.]+", "-", match.group("name")).lower() == CUP


def pin_from(requirements: cabc.Sequence[str], *, site: str) -> str:
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
    candidates = [item for item in requirements if names_cuprum(item)]
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


def declared_pin(pyproject_text: str) -> str:
    """Return the exact cuprum version ``pyproject.toml`` declares.

    Parameters
    ----------
    pyproject_text : str
        The raw text of ``pyproject.toml``, parsed here rather than by the
        caller.

    Returns
    -------
    str
        The version the repository's own requirement pins.

    Raises
    ------
    SelectionError
        If the requirement is missing, or is not a single exact pin.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by the delegated pin_from
    document = tomllib.loads(pyproject_text)
    requirements = document["project"]["dependencies"]
    return pin_from(requirements, site="pyproject.toml [project] dependencies")
