"""Contract-test that both coverage lanes measure on one Python.

The pull-request lane ratchets against the baseline the push-to-main publisher
writes, and a figure measured on one interpreter is not comparable with one
measured on another. The shared ``generate-coverage`` action builds its
environment on the Python the job put on ``PATH`` unless the job names one, so
the lanes agree only while their ``actions/setup-python`` steps do. Every job
that runs the coverage action must therefore set Python up before that step,
in the same job, and every such job must set up the same version. A setup step
in another job, or after the coverage step, puts nothing on the coverage
step's ``PATH`` and does not count.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml

from tests.workflow_contracts.test_coverage_ownership import (
    GENERATE_COVERAGE_ANY_REF,
    YamlValue,
    _coverage_steps,
    _jobs,
    _load_workflow,
    _step_inputs,
    _steps,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

SETUP_PYTHON_ACTION = "actions/setup-python@"


def _action(step: dict[str, YamlValue]) -> str:
    """Return a step's action reference, or empty for a ``run:`` step."""
    uses = step.get("uses")
    return uses if isinstance(uses, str) else ""


def _setups_before_coverage(path: Path, job_name: str) -> list[str]:
    """Return the Python versions a job sets up before its coverage step.

    Returns
    -------
    list of str
        The ``python-version`` of each ``setup-python`` step that precedes the
        job's first shared-action coverage step.
    """
    job = _jobs(_load_workflow(path))[job_name]
    assert isinstance(job, dict), f"{path.name}:{job_name} must be a job mapping"
    steps = _steps(typ.cast("dict[str, YamlValue]", job))
    coverage = next(
        index
        for index, step in enumerate(steps)
        if GENERATE_COVERAGE_ANY_REF.match(_action(step))
    )
    return [
        str(_step_inputs(step).get("python-version"))
        for step in steps[:coverage]
        if _action(step).startswith(SETUP_PYTHON_ACTION)
    ]


def test_every_coverage_job_sets_up_python_before_measuring() -> None:
    """Each coverage job installs the Python it measures on, ahead of the step."""
    missing = [
        f"{step.path.name}:{step.job}"
        for step in _coverage_steps()
        if not _setups_before_coverage(step.path, step.job)
    ]

    assert _coverage_steps(), "no coverage step found, so this contract proves nothing"
    assert missing == [], f"these coverage jobs set up no Python first: {missing}"


def test_both_lanes_measure_on_one_python() -> None:
    """Every coverage job, in both lanes, sets up one and the same version."""
    requested = {
        version
        for step in _coverage_steps()
        for version in _setups_before_coverage(step.path, step.job)
    }

    assert len(requested) == 1, f"coverage lanes set up {sorted(requested)}"


_SETUP = {
    "uses": f"{SETUP_PYTHON_ACTION}{'0' * 40}",
    "with": {"python-version": "3.13"},
}
_COVERAGE = {
    "uses": f"leynos/shared-actions/.github/actions/generate-coverage@{'0' * 40}"
}
_RUN = {"run": "make lint"}


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([_SETUP, _RUN, _COVERAGE], ["3.13"]),
        ([_COVERAGE, _SETUP], []),
        ([_RUN, _COVERAGE], []),
    ],
    ids=["before-the-step", "after-the-step", "none"],
)
def test_only_setups_before_the_coverage_step_count(
    tmp_path: Path, steps: list[dict[str, object]], expected: list[str]
) -> None:
    """A setup step counts only when it runs before coverage in the same job."""
    workflow = tmp_path / "coverage.yml"
    workflow.write_text(
        yaml.safe_dump({"on": "push", "jobs": {"cov": {"steps": steps}}}),
        encoding="utf-8",
    )

    assert _setups_before_coverage(workflow, "cov") == expected, (
        "only setup-python steps before the coverage step, in its job, count"
    )
