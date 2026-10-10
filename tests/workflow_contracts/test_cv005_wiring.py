"""Contract for the wiring of the shared CV-005 contract check.

The CV-005 clauses live in ``leynos/shared-actions`` (``cv005-contracts``) and
the library's own suite proves them. What the library cannot prove is that this
repository calls it: that the Makefile names a full commit, runs
``check --repository .`` under the Python the library needs, that
``.github/cv005.toml`` names this repository, that ``make all`` includes the
target, and that CI runs it. Each of those is read here by running ``make -n``
and parsing the workflow, so removing or misspelling any of them fails a test.

Run via ``make test``.
"""

import re
import tomllib
from pathlib import Path

import yaml
from plumbum import local

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "leynos/lading"
TARGET = "test-workflow-contracts"
SOURCE = (
    "git+https://github.com/leynos/shared-actions@{ref}"
    "#subdirectory=packages/cv005-contracts"
)
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
PIN = re.compile(r"^CV005_CONTRACTS_REF \?= (\S+)$", re.MULTILINE)


def _make_n(target: str) -> str:
    """Return the commands ``make -n TARGET`` would run.

    Returns
    -------
    str
        The dry-run output, one command per line.

    Examples
    --------
    ``_make_n("test-workflow-contracts")`` returns the ``uv tool run`` line that
    runs ``cv005-contracts check --repository .``, followed by the remaining
    pytest contracts.

    """
    with local.cwd(ROOT):
        return local["make"]("-n", target)


def _pinned_commit() -> str:
    """Return the commit ``CV005_CONTRACTS_REF`` names in the Makefile.

    Returns
    -------
    str
        The value assigned to ``CV005_CONTRACTS_REF``.

    Examples
    --------
    With ``CV005_CONTRACTS_REF ?= 8897779...`` in the Makefile, this returns the
    forty-character hash, which the tests then compare with the checker's
    source.

    """
    match = PIN.search((ROOT / "Makefile").read_text("utf-8"))
    assert match is not None, "the Makefile must set CV005_CONTRACTS_REF"
    return match[1]


def test_the_pin_is_a_full_commit() -> None:
    """Refuse a branch, tag or abbreviated commit as the checker's source."""
    pin = _pinned_commit()
    assert FULL_COMMIT.fullmatch(pin), f"{pin!r} is not a full commit hash"


def test_the_target_runs_the_pinned_checker_on_this_repository() -> None:
    """Run ``check --repository .`` from the pinned source under Python 3.14."""
    commands = _make_n(TARGET)
    source = SOURCE.format(ref=_pinned_commit())
    runs = [line for line in commands.splitlines() if "cv005-contracts" in line]
    assert len(runs) == 1, f"expected one checker invocation, got {runs!r}"
    run = runs[0]
    assert "uv tool run" in run, run
    assert "--python 3.14" in run, run
    assert f"--from '{source}'" in run, run
    assert run.rstrip().endswith("cv005-contracts check --repository ."), run


def test_the_repository_parameter_names_this_repository() -> None:
    """Hold ``.github/cv005.toml`` to this repository's name."""
    config = tomllib.loads((ROOT / ".github" / "cv005.toml").read_text("utf-8"))
    assert config.get("repository") == REPOSITORY, config


def test_make_all_includes_the_target() -> None:
    """Run the checker from the comprehensive gate as well as on its own."""
    source = SOURCE.format(ref=_pinned_commit())
    commands = _make_n("all")
    assert f"--from '{source}'" in commands, commands
    assert "cv005-contracts check --repository ." in commands, commands


def test_make_test_includes_the_target() -> None:
    """Run the checker from the standard test gate as well."""
    commands = _make_n("test")
    assert "cv005-contracts check --repository ." in commands, commands


def test_ci_runs_the_target_unconditionally() -> None:
    """Require a CI step that runs the target with no condition on it."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    )
    holders = [
        (job, step)
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if f"make {TARGET}" in str(step.get("run", ""))
    ]
    assert holders, f"ci.yml must run `make {TARGET}` in a step"
    assert all("if" not in step for _, step in holders), holders
    assert all("if" not in job for job, _ in holders), holders
    assert all("continue-on-error" not in step for _, step in holders), holders
    assert all("continue-on-error" not in job for job, _ in holders), holders
    assert all(
        str(step.get("run", "")).strip() == f"make {TARGET}" for _, step in holders
    ), holders
