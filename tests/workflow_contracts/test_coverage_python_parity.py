"""Contract-test that both coverage lanes measure on one Python.

The pull-request lane ratchets against the baseline the push-to-main publisher
writes, and a figure measured on one interpreter is not comparable with one
measured on another. The shared ``generate-coverage`` action builds its
environment on the Python the job put on ``PATH`` unless the job names one, so
the lanes agree only while their ``actions/setup-python`` steps do.

A job's steps run in order, and each ``setup-python`` step replaces the Python
on ``PATH`` for the steps after it. Every coverage call therefore measures on
the most recent setup before it in its own job; a setup in another job, or
after the call, does not count. Every call must follow a setup naming a
``python-version``, and every call in both lanes must measure on one version.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml

from tests.workflow_contracts.test_coverage_ownership import (
    GENERATE_COVERAGE_ANY_REF,
    YamlValue,
    _jobs,
    _load_workflow,
    _step_inputs,
    _steps,
    _workflow_paths,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

SETUP_PYTHON_ACTION = "actions/setup-python@"


def _action(step: dict[str, YamlValue]) -> str:
    """Return a step's action reference, or empty for a ``run:`` step."""
    uses = step.get("uses")
    return uses if isinstance(uses, str) else ""


def _pythons_at_coverage(path: Path) -> list[tuple[str, str]]:
    """Return the Python on ``PATH`` at every coverage call in one workflow.

    Returns
    -------
    list of tuple of (str, str)
        One ``(job, version)`` pair per shared-action coverage call, in
        workflow order. The version is empty when no ``setup-python`` step
        precedes the call in its job, or the latest one names no
        ``python-version``.
    """
    runs: list[tuple[str, str]] = []
    for job_name, job in _jobs(_load_workflow(path)).items():
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            continue
        on_path = ""
        for step in _steps(typ.cast("dict[str, YamlValue]", job)):
            if _action(step).startswith(SETUP_PYTHON_ACTION):
                on_path = str(_step_inputs(step).get("python-version") or "")
            elif GENERATE_COVERAGE_ANY_REF.match(_action(step)):
                runs.append((job_name, on_path))
    return runs


def _all_coverage_runs() -> list[tuple[str, str]]:
    """Return every ``(workflow:job, version)`` coverage call in the repository."""
    return [
        (f"{path.name}:{job}", version)
        for path in _workflow_paths()
        for job, version in _pythons_at_coverage(path)
    ]


def test_every_coverage_call_measures_on_a_named_python() -> None:
    """Each coverage call follows, in its job, a setup naming its version."""
    runs = _all_coverage_runs()
    unnamed = [job for job, version in runs if not version]

    assert runs, "no coverage call found, so this contract proves nothing"
    assert unnamed == [], (
        f"these coverage calls reach the action without a setup-python step "
        f"naming a python-version: {unnamed}"
    )


def test_both_lanes_measure_on_one_python() -> None:
    """Every coverage call, in both lanes, measures on one and the same version."""
    requested = {version for _job, version in _all_coverage_runs()}

    assert len(requested) == 1, f"coverage lanes measure on {sorted(requested)}"


_SETUP = {"uses": f"{SETUP_PYTHON_ACTION}{'0' * 40}"}
_COVERAGE = {
    "uses": f"leynos/shared-actions/.github/actions/generate-coverage@{'0' * 40}"
}
_RUN = {"run": "make lint"}


def _setup(version: str) -> dict[str, object]:
    """Return a setup-python step requesting ``version``."""
    return {**_SETUP, "with": {"python-version": version}}


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        ({"cov": [_setup("3.13"), _RUN, _COVERAGE]}, [("cov", "3.13")]),
        ({"cov": [_COVERAGE, _setup("3.13")]}, [("cov", "")]),
        ({"other": [_setup("3.13")], "cov": [_COVERAGE]}, [("cov", "")]),
        (
            {"cov": [_setup("3.13"), _COVERAGE, _setup("3.14"), _COVERAGE]},
            [("cov", "3.13"), ("cov", "3.14")],
        ),
        ({"cov": [_SETUP, _COVERAGE]}, [("cov", "")]),
    ],
    ids=[
        "before-the-call",
        "after-the-call",
        "another-job",
        "latest-setup-per-call",
        "setup-without-version",
    ],
)
def test_each_call_reads_the_latest_setup_before_it(
    tmp_path: Path,
    jobs: dict[str, list[dict[str, object]]],
    expected: list[tuple[str, str]],
) -> None:
    """A call measures on its job's most recent named setup, or on nothing."""
    workflow = tmp_path / "coverage.yml"
    # Keep the jobs in the order given: a setup in an earlier job must not
    # leak into a later one, and sorting would put "cov" before "other".
    workflow.write_text(
        yaml.safe_dump(
            {
                "on": "push",
                "jobs": {name: {"steps": steps} for name, steps in jobs.items()},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert _pythons_at_coverage(workflow) == expected, (
        "a call measures on the latest named setup before it, in its own job"
    )
