"""Contract tests for the single Python baseline the gateways derive from.

``PYTHON_BASELINE`` in the Makefile is the one place the project's Python
version is stated. Everything else -- Ruff's ``target-version``, Pylint's
``py-version``, the interpreters ``uv tool run`` manages, and
``ty --python-version`` -- is a mirror of it, and a mirror that drifts is how
a baseline bump silently becomes a partial one. Raising the baseline while
Ruff still parses at the old version leaves new syntax unlinted; lowering it
while the managed interpreters stay advanced leaves the gate parsing a
language the package no longer promises to run on.

Every assertion here derives its expected spelling from the baseline it reads,
so bumping the value in the Makefile is the whole edit and this module needs
no change. That is the point: a test with its own copy of "3.14" would be one
more mirror to drift.
"""

import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"


def _makefile() -> str:
    """Return the Makefile with continuations joined into single lines.

    Returns
    -------
    str
        The Makefile's text, with each backslash-newline continuation
        collapsed so a multi-line assignment reads as one line.
    """
    return MAKEFILE_PATH.read_text(encoding="utf-8").replace("\\\n", " ")


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
    match = re.search(
        rf"^{re.escape(name)}\s*\??=\s*(.+)$", _makefile(), flags=re.MULTILINE
    )
    assert match is not None, f"{name} is not defined in the Makefile"
    return match.group(1).strip()


def test_baseline_is_the_single_source_for_every_gateway() -> None:
    """Each gateway must name ``PYTHON_BASELINE`` rather than its own version."""
    baseline = _makefile_variable("PYTHON_BASELINE")
    assert re.fullmatch(r"\d+\.\d+", baseline), (
        f"PYTHON_BASELINE must be a major.minor version, found {baseline!r}"
    )
    for variable in ("PYLINT_PYTHON", "DF12_PYTHON"):
        assert _makefile_variable(variable) == "$(PYTHON_BASELINE)", (
            f"{variable} must track PYTHON_BASELINE; a private copy is a "
            f"second source of truth that a baseline bump would miss"
        )


def test_gateways_pass_the_baseline_to_their_interpreters() -> None:
    """The managed interpreters and ``ty`` must be told the baseline value."""
    fragments = (
        "--python $(PYLINT_PYTHON)",
        "--python $(DF12_PYTHON)",
        "--python $(PYTHON_BASELINE)",
        "--py-version=$(PYTHON_BASELINE)",
        "--python-version $(PYTHON_BASELINE)",
    )
    makefile = _makefile()
    for fragment in fragments:
        assert fragment in makefile, (
            f"a gateway has stopped passing the baseline to its interpreter: "
            f"missing {fragment!r}"
        )


def test_pyproject_mirrors_the_baseline_for_the_linters() -> None:
    """Ruff and Pylint must parse at the baseline the Makefile declares."""
    baseline = _makefile_variable("PYTHON_BASELINE")
    ruff_tag = "py" + baseline.replace(".", "")
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    assert config["tool"]["ruff"]["target-version"] == ruff_tag, (
        f"Ruff's target-version must be {ruff_tag!r} to match "
        f"PYTHON_BASELINE={baseline}"
    )
    assert config["tool"]["pylint"]["main"]["py-version"] == baseline, (
        f"Pylint's py-version must be {baseline!r} to match PYTHON_BASELINE"
    )


def test_package_metadata_requires_the_baseline() -> None:
    """The declared runtime floor must be the version the gate parses with."""
    baseline = _makefile_variable("PYTHON_BASELINE")
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    requires_python = config["project"]["requires-python"]
    assert requires_python == f">={baseline}", (
        f"requires-python must be >={baseline} to match PYTHON_BASELINE; "
        f"found {requires_python!r}"
    )


def test_lading_is_a_python_source_root() -> None:
    """The production package must be gated in its own right.

    ``PYTHON_SOURCE_ROOTS`` is discovered rather than enumerated, and
    ``lading`` is listed explicitly because it is not reached through any
    other tree. A roots list that omitted it would leave the production
    package entirely ungated while the whole suite still passed.
    """
    roots = _makefile_variable("PYTHON_SOURCE_ROOTS").split()
    assert "lading" in roots, (
        f"the production package must be linted and typechecked as its own "
        f"root, not reached through another tree; roots={roots!r}"
    )
