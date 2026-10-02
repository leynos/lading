"""Contract tests for the Ruff pin, which is declared in three places.

Ruff's version is written three times: as the ``ruff==`` development
dependency in ``pyproject.toml``, as ``RUFF_VERSION`` in the Makefile, and as
the ``uv tool install ruff==`` line in the Continuous Integration (CI)
workflow. Dependabot moves the first one automatically; the other two move
only when a contributor moves them. Left alone they drift, and a drifted pin
is a version-skew failure: the rule sets differ between Ruff releases, so
``make lint`` and CI would disagree about what the same source is allowed to
contain, with neither run obviously wrong.

The three sites are held equal here so the drift is a failing test rather than
a red Continuous Integration run on somebody else's pull request.
"""

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"
CI_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/ci.yml"

pytestmark = pytest.mark.skipif(
    not (MAKEFILE_PATH.exists() and PYPROJECT_PATH.exists()),
    reason="Makefile or lint configuration not present in this working copy",
)

_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


def _makefile_variable(name: str) -> str:
    """Return the value assigned to a Makefile variable.

    Parameters
    ----------
    name : str
        The variable name, assigned with ``=`` or ``?=``.

    Returns
    -------
    str
        The assigned value, with surrounding whitespace removed.
    """
    text = MAKEFILE_PATH.read_text(encoding="utf-8").replace("\\\n", " ")
    match = re.search(
        rf"^{re.escape(name)}\s*\??=\s*(.+?)\s*$", text, flags=re.MULTILINE
    )
    assert match is not None, f"{name} is not defined in the Makefile"
    return match.group(1).strip()


def _pyproject_ruff_pin() -> str:
    """Return the version of the ``ruff==`` development dependency.

    Returns
    -------
    str
        The pinned version, without the distribution name.
    """
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = re.search(r'"ruff==(\d+\.\d+\.\d+)"', text)
    assert match is not None, (
        "pyproject.toml must pin ruff with an exact `ruff==` dev dependency"
    )
    return match.group(1)


def _ci_ruff_pin() -> str:
    """Return the version the CI workflow installs as a uv tool.

    Returns
    -------
    str
        The pinned version, without the distribution name.
    """
    text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    match = re.search(r"uv tool install ruff==(\d+\.\d+\.\d+)", text)
    assert match is not None, (
        "the CI workflow must install ruff with an exact `ruff==` pin"
    )
    return match.group(1)


def test_ruff_version_is_pinned_exactly_everywhere() -> None:
    """Every site must name an exact release, not a floating constraint."""
    for label, version in (
        ("RUFF_VERSION", _makefile_variable("RUFF_VERSION")),
        ("pyproject.toml", _pyproject_ruff_pin()),
        (".github/workflows/ci.yml", _ci_ruff_pin()),
    ):
        assert _VERSION_PATTERN.fullmatch(version), (
            f"{label} must pin Ruff to an exact release, found {version!r}"
        )


def test_all_ruff_pins_agree() -> None:
    """The three declarations must name the same release.

    A mismatch makes ``make lint`` and Continuous Integration apply different
    rule sets to the same source, so one can pass where the other fails.
    """
    makefile_version = _makefile_variable("RUFF_VERSION")
    pyproject_version = _pyproject_ruff_pin()
    ci_version = _ci_ruff_pin()
    assert makefile_version == pyproject_version, (
        f"the Makefile invokes Ruff {makefile_version} but the development "
        f"dependency is {pyproject_version}"
    )
    assert ci_version == pyproject_version, (
        f"Continuous Integration installs Ruff {ci_version} but the "
        f"development dependency is {pyproject_version}"
    )


def test_the_makefile_invokes_the_pinned_version() -> None:
    """``RUFF`` must run the version ``RUFF_VERSION`` names."""
    command = _makefile_variable("RUFF")
    assert "tool run --from ruff==$(RUFF_VERSION) ruff" in command, (
        f"RUFF must provision the pinned release through `uv tool run`, "
        f"found {command!r}"
    )
