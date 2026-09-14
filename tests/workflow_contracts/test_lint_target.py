"""Contract tests for the Interrogate stages of the ``make lint`` target.

The lint target must enforce the absolute 100% docstring threshold in two
passes: an unexempted pass over the production ``lading`` package, and a pass
over ``tests`` and ``scripts`` that carries the shape-based nested-definition
exemptions on its command line. Exemptions must stay scoped to that second
invocation so a nested production helper can never slip past the strict gate.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

MAKE_BINARY = shutil.which("make")

pytestmark = pytest.mark.skipif(
    MAKE_BINARY is None,
    reason="make executable not found on PATH",
)

#: The shape-based exemptions that only the tests/scripts pass may carry.
NESTED_EXEMPTION_FLAGS = ("--ignore-nested-functions", "--ignore-nested-classes")


def _interrogate_lint_passes() -> list[str]:
    """Expand ``make -n lint`` and return its Interrogate command lines."""
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv list using the PATH-resolved make binary
        [MAKE_BINARY, "-n", "lint"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    joined = completed.stdout.replace("\\\n", " ")
    return [
        line
        for line in joined.splitlines()
        if "interrogate" in line and "--fail-under 100" in line
    ]


def test_lint_runs_two_absolute_interrogate_passes() -> None:
    """Both Interrogate passes must gate their scopes at exactly 100%."""
    passes = _interrogate_lint_passes()
    assert len(passes) == 2, passes


def test_production_interrogate_pass_has_no_nested_exemptions() -> None:
    """The production pass must keep enforcing docstrings on nested definitions."""
    production_passes = [
        line for line in _interrogate_lint_passes() if " lading" in line
    ]
    assert len(production_passes) == 1, production_passes
    for flag in NESTED_EXEMPTION_FLAGS:
        assert flag not in production_passes[0], flag


def test_tests_scripts_interrogate_pass_exempts_nested_definitions() -> None:
    """Only the tests/scripts pass may carry the shape-based exemptions."""
    test_passes = [
        line
        for line in _interrogate_lint_passes()
        if " tests" in line and " scripts" in line
    ]
    assert len(test_passes) == 1, test_passes
    for flag in NESTED_EXEMPTION_FLAGS:
        assert flag in test_passes[0], flag


def test_interrogate_config_does_not_weaken_the_production_gate() -> None:
    """Nested-definition exemptions must stay out of the shared Interrogate config."""
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
    assert "[tool.interrogate]" in pyproject, "Interrogate config section missing"
    for flag in NESTED_EXEMPTION_FLAGS:
        config_key = flag.removeprefix("--").replace("-", "_")
        assert f"{config_key} = true" not in pyproject, config_key
