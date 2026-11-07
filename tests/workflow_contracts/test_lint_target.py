"""Contract tests for the docstring-coverage stage of the ``make lint`` target.

``make lint`` must reach one ``lint-docstrings`` run over ``DOCSTRING_TARGETS``,
at a threshold of 100 with no nested-definition exemption on its command line,
and with a guard that fails on any ``MISSED`` row: interrogate rounds
its percentage to one decimal, so the threshold alone can pass a lone gap in a
large repository. These tests read the recipe's variables and run the target, so
a dropped call, a relaxed flag or a guard that stops failing breaks a test
rather than silently restoring the gap.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPO_ROOT / "Makefile"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

MAKE_BINARY = shutil.which("make")

pytestmark = pytest.mark.skipif(
    MAKE_BINARY is None,
    reason="make executable not found on PATH",
)

#: The shape-based exemptions that no gate or shared config may carry.
NESTED_EXEMPTION_FLAGS = ("--ignore-nested-functions", "--ignore-nested-classes")

#: Variables a caller or a parent Make could set that would change the target.
_MAKE_STATE = frozenset({
    "MAKEFLAGS",
    "MFLAGS",
    "GNUMAKEFLAGS",
    "MAKELEVEL",
    "MAKEOVERRIDES",
    "UV",
})
#: Interrogate 1.7.0 skips a path whose first component below a target begins
#: with one of these (virtual environments and version-control directories).
_EXCLUDED_PREFIXES = (".tox", ".venv", "venv", ".git", ".hg")


def _makefile_text() -> str:
    """Return the Makefile source."""
    return MAKEFILE_PATH.read_text(encoding="utf-8")


def _variable(name: str) -> str:
    """Return the value a ``NAME ?=`` or ``NAME =`` assignment gives, joined.

    Returns
    -------
    str
        The assigned value, with continuation lines joined by single spaces.
    """
    pattern = re.compile(rf"^{name}\s*[:?]?=\s*((?:.*\\\n)*.*)$", re.MULTILINE)
    match = pattern.search(_makefile_text())
    assert match, f"the Makefile must assign {name}"
    return re.sub(r"\\\n\s*", " ", match.group(1)).strip()


def _recipe_commands(target: str) -> list[list[str]]:
    """Return the recipe lines of *target*, each split into words.

    Returns
    -------
    list[list[str]]
        One word list per non-blank recipe line.
    """
    recipe = re.search(
        rf"^{re.escape(target)}:.*\n((?:\t.*\n|\n)*)", _makefile_text(), re.MULTILINE
    )
    assert recipe, f"the Makefile must declare a {target} recipe"
    return [line.split() for line in recipe.group(1).splitlines() if line.strip()]


def _tracked_python_files() -> list[str]:
    """Return the Git-tracked ``.py`` files, relative to the repository root.

    Git is the authority on what the repository owns: a walk would also see the
    virtual environments the gates create beside the sources.

    Returns
    -------
    list[str]
        The tracked paths.
    """
    output = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "*.py"],  # ruff: ignore[start-process-with-partial-path] - git
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [name for name in output.split("\0") if name]


def _assignment(name: str, *words: Path | str) -> str:
    """Build a Make command-line assignment whose value is a shell command.

    Each word is shell-quoted and each dollar sign doubled, so Make hands the
    recipe's shell exactly the words given.

    Returns
    -------
    str
        The ``NAME=value`` argument.
    """
    value = " ".join(shlex.quote(str(word)) for word in words)
    return f"{name}={value.replace('$', '$$')}"


def _make_docstrings(*assignments: str) -> subprocess.CompletedProcess[str]:
    """Run ``make lint-docstrings`` with the given variable assignments.

    A parent Make's flags and a caller's ``UV`` or ``DOCSTRING_*`` overrides
    would change what the target runs, so those variables are removed from the
    child environment.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished ``make`` process with its captured output.
    """
    assert MAKE_BINARY is not None, "make executable not found on PATH"
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in _MAKE_STATE and not name.startswith("DOCSTRING_")
    }
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv using the PATH-resolved make binary
        [MAKE_BINARY, "-C", str(REPO_ROOT), "lint-docstrings", *assignments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _run_stub(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run ``make lint-docstrings`` with the tool replaced by a stub script.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished ``make`` process with its captured output.
    """
    stub = tmp_path / "stub.sh"
    stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n'
        + textwrap.dedent(script),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return _make_docstrings(_assignment("DOCSTRING_TOOL", stub))


def _run_real_tool(
    tmp_path: Path, definitions: int, undocumented: int
) -> subprocess.CompletedProcess[str]:
    """Run ``make lint-docstrings`` with the real tool over a generated package.

    The package holds ``definitions`` documented functions and then
    ``undocumented`` functions without a docstring. The tool is the one the
    test environment installs, so the run needs no network.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished ``make`` process with its captured output.
    """
    package = tmp_path / "generated"
    package.mkdir()
    documented = "".join(
        f'def documented_{n}() -> None:\n    """Document {n}."""\n\n\n'
        for n in range(definitions)
    )
    bare = "".join(
        f"def bare_{n}() -> None:\n    pass\n\n\n" for n in range(undocumented)
    )
    (package / "mod.py").write_text(
        '"""Generated module."""\n\n\n' + documented + bare, encoding="utf-8"
    )
    return _make_docstrings(
        _assignment("DOCSTRING_TOOL", sys.executable, "-m", "interrogate"),
        _assignment("DOCSTRING_TARGETS", package),
    )


def test_lint_runs_the_docstring_target() -> None:
    """Dropping the call would remove the gate from ``make lint``."""
    assert any(
        command[:1] == ["$(MAKE)"] and command[-1] == "lint-docstrings"
        for command in _recipe_commands("lint")
    ), "lint must run the lint-docstrings target through $(MAKE)"


def test_the_flags_demand_a_full_exact_report() -> None:
    """Exactly: every scanned definition printed and a threshold of 100.

    Equality, not a deny-list: any other flag (an ignore or exclude option, or
    ``-q`` and ``-o``, which suppress or redirect the report the guard reads)
    could relax the gate or blind the MISSED guard, nested exemptions included.
    """
    flags = _variable("DOCSTRING_FLAGS").split()
    assert flags == ["-vv", "--fail-under", "100"], flags


def test_every_tracked_python_file_is_under_a_target() -> None:
    """A tracked Python file outside ``DOCSTRING_TARGETS`` is not checked."""
    targets = [Path(t) for t in _variable("DOCSTRING_TARGETS").split()]
    uncovered = [
        name
        for name in _tracked_python_files()
        if not any(Path(name).is_relative_to(target) for target in targets)
    ]
    assert not uncovered, f"add these to DOCSTRING_TARGETS: {uncovered}"


def test_no_tracked_file_hides_behind_a_default_exclusion() -> None:
    """A tracked file interrogate skips by default would never be measured."""
    targets = [Path(t) for t in _variable("DOCSTRING_TARGETS").split()]
    hidden = [
        name
        for name in _tracked_python_files()
        for target in targets
        if Path(name).is_relative_to(target)
        and Path(name).relative_to(target).parts[0].startswith(_EXCLUDED_PREFIXES)
    ]
    assert not hidden, f"interrogate skips these by default: {hidden}"


def test_no_nested_exemption_survives_in_the_makefile() -> None:
    """The Makefile must not pass a nested-definition exemption anywhere."""
    makefile = _makefile_text()
    for flag in NESTED_EXEMPTION_FLAGS:
        assert flag not in makefile, flag


def test_interrogate_config_does_not_weaken_the_gate() -> None:
    """The parsed ``[tool.interrogate]`` table must not enable a nested exemption."""
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    table = pyproject["tool"]["interrogate"]
    enabled = {key.replace("_", "-") for key, value in table.items() if value is True}
    for flag in NESTED_EXEMPTION_FLAGS:
        assert flag.removeprefix("--") not in enabled, flag


def test_a_documented_package_passes_the_real_gate(tmp_path: Path) -> None:
    """A package whose every definition has a docstring passes the real tool."""
    result = _run_real_tool(tmp_path, definitions=5, undocumented=0)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_undocumented_function_fails_the_real_gate(tmp_path: Path) -> None:
    """One undocumented function fails the real tool and reports a MISSED row."""
    result = _run_real_tool(tmp_path, definitions=5, undocumented=1)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "MISSED" in result.stdout, result.stdout


def test_a_gap_hidden_by_rounding_still_fails_the_target(tmp_path: Path) -> None:
    """One undocumented function beside 4,000 documented ones still fails.

    The tool alone accepts the report (its percentage rounds to 100.0), so this
    proves the MISSED-row guard is what rejects the lone gap.
    """
    result = _run_real_tool(tmp_path, definitions=4000, undocumented=1)
    assert "RESULT: PASSED" in result.stdout, "the rounded percentage must pass"
    assert result.returncode != 0, "the MISSED guard must still fail the target"
    assert "undocumented definitions remain" in result.stderr, result.stderr


def test_the_target_passes_when_the_tool_reports_no_gap(tmp_path: Path) -> None:
    """A clean report and a zero status pass."""
    result = _run_stub(tmp_path, "echo '| f.py | COVERED |'\nexit 0\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "| f.py | COVERED |" in result.stdout, "the report must be printed"


def test_the_target_passes_the_configured_flags_and_targets(tmp_path: Path) -> None:
    """The tool receives exactly the configured flags, then the targets."""
    _run_stub(tmp_path, "exit 0\n")
    received = (tmp_path / "args.txt").read_text(encoding="utf-8").split()
    expected = (
        _variable("DOCSTRING_FLAGS").split() + _variable("DOCSTRING_TARGETS").split()
    )
    assert received == expected, f"the tool received {received}, not {expected}"


def test_the_target_fails_when_the_tool_fails(tmp_path: Path) -> None:
    """A failing tool fails the target."""
    result = _run_stub(tmp_path, "echo 'FAILED'\nexit 1\n")
    assert result.returncode != 0, "a failing tool must fail the target"
    assert "FAILED" in result.stdout, "the failing report must still be printed"


@pytest.mark.parametrize(
    "cell",
    ["|  MISSED |", "|   MISSED    |"],
    ids=["interrogate-default-padding", "wider-padding"],
)
def test_the_target_fails_on_a_missed_row_the_percentage_hid(
    tmp_path: Path, cell: str
) -> None:
    """A MISSED row in a report from a successful run still fails the target.

    The guard must not depend on how many spaces pad the status cell.
    """
    result = _run_stub(
        tmp_path,
        f"echo '| f.main (L3) {cell}'\necho 'RESULT: PASSED'\nexit 0\n",
    )
    assert result.returncode != 0, "a MISSED row must fail the target"
    assert "undocumented definitions remain" in result.stderr, result.stderr
    assert "f.main (L3)" in result.stdout, "the report must still be printed"
