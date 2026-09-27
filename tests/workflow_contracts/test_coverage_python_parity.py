"""Contract-test that every coverage call measures on one declared Python.

The pull-request lane ratchets against the baseline the push-to-main publisher
writes, and a figure measured on one interpreter is not comparable with one
measured on another. The shared ``generate-coverage`` action chooses its
interpreter in a fixed order: its ``python-version`` input, then
``UV_PYTHON``, then the first entry of ``.python-version``, then the
``python3`` the job put on ``PATH``, which is the most recent
``actions/setup-python`` step before the call in its job.

Every coverage call in every workflow must therefore declare at least one of
those sources, every source it declares must name the same version (a
higher-priority value silently overriding a lower one is how lanes drift), and
every call must measure on that one version. A setup step guarded by ``if:``
or allowed to fail with ``continue-on-error`` may not run, so it declares
nothing; a setup in another job, or after the call, does not count.
"""

from __future__ import annotations

import re
import typing as typ

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from tests.workflow_contracts.test_coverage_ownership import (
    REPOSITORY_ROOT,
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
#: A pinned, a relative (``./``) or a same-repository (``$/``) reference.
GENERATE_COVERAGE = re.compile(r"(?:^|/)\.github/actions/generate-coverage(?:@|$)")
#: The resolver's order, highest priority first.
SOURCES = ("input", "UV_PYTHON", ".python-version", "setup-python")

type Mapping = dict[str, YamlValue]


class CoverageCall(typ.NamedTuple):
    """One coverage call and the version each resolver source declares for it."""

    job: str
    sources: dict[str, str]

    @property
    def declared(self) -> dict[str, str]:
        """The sources that name a version."""
        return {name: version for name, version in self.sources.items() if version}

    @property
    def effective(self) -> str:
        """The version the resolver would choose, or empty."""
        return next(iter(self.declared.values()), "")


def _action(step: Mapping) -> str:
    """Return a step's action reference, or empty for a ``run:`` step."""
    uses = step.get("uses")
    return uses if isinstance(uses, str) else ""


def _python_version_entry(path: Path) -> str:
    """Return the first non-comment entry of a ``.python-version`` file, or empty."""
    if not path.is_file():
        return ""
    entries = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return next((entry for entry in entries if entry and not entry.startswith("#")), "")


def _declared_by_setup(step: Mapping) -> str:
    """Return the version a setup-python step reliably puts on ``PATH``, or empty."""
    if "if" in step or step.get("continue-on-error"):
        return ""
    return str(_step_inputs(step).get("python-version") or "")


def _uv_python(*scopes: Mapping) -> str:
    """Return the innermost ``UV_PYTHON`` among step, job and workflow scopes."""
    for scope in scopes:
        env = scope.get("env")
        value = env.get("UV_PYTHON") if isinstance(env, dict) else None
        if value:
            return str(value)
    return ""


def _job_calls(
    name: str, job: Mapping, workflow: Mapping, python_version: str
) -> list[CoverageCall]:
    """Return one job's coverage calls, reading its steps in order."""
    calls: list[CoverageCall] = []
    on_path = ""
    for step in _steps(job):
        action = _action(step)
        if action.startswith(SETUP_PYTHON_ACTION):
            on_path = _declared_by_setup(step)
        elif GENERATE_COVERAGE.search(action):
            versions = (
                str(_step_inputs(step).get("python-version") or ""),
                _uv_python(step, job, workflow),
                python_version,
                on_path,
            )
            calls.append(CoverageCall(name, dict(zip(SOURCES, versions, strict=True))))
    return calls


def _coverage_calls(path: Path, python_version: str = "") -> list[CoverageCall]:
    """Return every coverage call in one workflow, in workflow order.

    Returns
    -------
    list of CoverageCall
        One entry per call, with every source's version in the resolver's
        priority order (empty where a source declares nothing).
    """
    workflow = typ.cast("Mapping", _load_workflow(path))
    return [
        call
        for name, job in _jobs(workflow).items()
        if isinstance(job, dict) and isinstance(job.get("steps"), list)
        for call in _job_calls(name, typ.cast("Mapping", job), workflow, python_version)
    ]


def _all_coverage_calls() -> dict[str, list[CoverageCall]]:
    """Return every workflow's coverage calls, keyed by workflow file name."""
    python_version = _python_version_entry(REPOSITORY_ROOT / ".python-version")
    return {
        path.name: _coverage_calls(path, python_version) for path in _workflow_paths()
    }


def test_every_coverage_call_declares_one_python() -> None:
    """Each call names a Python, and every source it declares agrees."""
    calls = [
        (f"{workflow}:{call.job}", call)
        for workflow, found in _all_coverage_calls().items()
        for call in found
    ]
    undeclared = [where for where, call in calls if not call.declared]
    conflicting = {
        where: call.declared
        for where, call in calls
        if len(set(call.declared.values())) > 1
    }

    assert calls, "no coverage call found, so this contract proves nothing"
    assert undeclared == [], (
        f"these calls measure on an undeclared Python: {undeclared}"
    )
    assert conflicting == {}, f"these calls declare conflicting versions: {conflicting}"


def test_both_lanes_measure_on_one_python() -> None:
    """Every coverage call, in every workflow, measures on one version."""
    effective = {
        call.effective for found in _all_coverage_calls().values() for call in found
    }

    assert len(effective) == 1, f"coverage calls measure on {sorted(effective)}"


_SETUP = {"uses": f"{SETUP_PYTHON_ACTION}{'0' * 40}"}
_COVERAGE = {
    "uses": f"leynos/shared-actions/.github/actions/generate-coverage@{'0' * 40}"
}
_RUN = {"run": "make lint"}


def _setup(version: str, **extra: object) -> dict[str, object]:
    """Return a setup-python step requesting ``version``."""
    return {**_SETUP, "with": {"python-version": version}, **extra}


def _write(tmp_path: Path, document: dict[str, object]) -> Path:
    """Write a fixture workflow, keeping its jobs in the order given."""
    workflow = tmp_path / "coverage.yml"
    workflow.write_text(
        yaml.safe_dump({"on": "push", **document}, sort_keys=False), encoding="utf-8"
    )
    return workflow


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        ({"cov": [_setup("3.13"), _RUN, _COVERAGE]}, ["3.13"]),
        ({"cov": [_COVERAGE, _setup("3.13")]}, [""]),
        ({"other": [_setup("3.13")], "cov": [_COVERAGE]}, [""]),
        (
            {"cov": [_setup("3.13"), _COVERAGE, _setup("3.14"), _COVERAGE]},
            ["3.13", "3.14"],
        ),
        ({"cov": [_SETUP, _COVERAGE]}, [""]),
        ({"cov": [_setup("3.13", **{"if": "false"}), _COVERAGE]}, [""]),
        ({"cov": [_setup("3.13", **{"continue-on-error": True}), _COVERAGE]}, [""]),
        (
            {"cov": [_setup("3.13"), {"uses": "./.github/actions/generate-coverage"}]},
            ["3.13"],
        ),
        (
            {"cov": [_setup("3.13"), {"uses": "$/.github/actions/generate-coverage"}]},
            ["3.13"],
        ),
    ],
    ids=[
        "before-the-call",
        "after-the-call",
        "another-job",
        "latest-setup-per-call",
        "setup-without-version",
        "conditional-setup",
        "fail-green-setup",
        "relative-action",
        "same-repository-action",
    ],
)
def test_each_call_reads_the_setup_that_reliably_precedes_it(
    tmp_path: Path, jobs: dict[str, list[dict[str, object]]], expected: list[str]
) -> None:
    """A call's setup-python source is its job's latest unconditional setup."""
    document = {"jobs": {name: {"steps": steps} for name, steps in jobs.items()}}
    calls = _coverage_calls(_write(tmp_path, document))

    assert [call.sources["setup-python"] for call in calls] == expected, (
        "a call reads the latest setup that always runs before it, in its job"
    )


@pytest.mark.parametrize(
    ("document", "declared"),
    [
        (
            {
                "jobs": {
                    "cov": {
                        "steps": [
                            _setup("3.14"),
                            {**_COVERAGE, "with": {"python-version": "3.13"}},
                        ]
                    }
                }
            },
            {"input": "3.13", "setup-python": "3.14"},
        ),
        (
            {
                "jobs": {
                    "cov": {
                        "env": {"UV_PYTHON": "3.13"},
                        "steps": [_setup("3.14"), _COVERAGE],
                    }
                }
            },
            {"UV_PYTHON": "3.13", "setup-python": "3.14"},
        ),
        (
            {
                "env": {"UV_PYTHON": "3.12"},
                "jobs": {
                    "cov": {
                        "env": {"UV_PYTHON": "3.13"},
                        "steps": [_setup("3.14"), _COVERAGE],
                    }
                },
            },
            {"UV_PYTHON": "3.13", "setup-python": "3.14"},
        ),
    ],
    ids=["input", "job-uv-python", "job-uv-python-wins-over-workflow"],
)
def test_higher_priority_sources_are_declared_beside_the_setup(
    tmp_path: Path, document: dict[str, object], declared: dict[str, str]
) -> None:
    """Every source the resolver reads is recorded, so a conflict is visible."""
    (call,) = _coverage_calls(_write(tmp_path, document))

    assert call.declared == declared, "each declared source is recorded"
    assert call.effective == next(iter(declared.values())), (
        "the effective version is the highest-priority declared source"
    )


_STEP = st.one_of(
    st.tuples(
        st.just("setup"), st.sampled_from(["3.12", "3.13", "3.14", ""]), st.booleans()
    ),
    st.tuples(st.just("coverage"), st.just(""), st.just(value=False)),
    st.tuples(st.just("run"), st.just(""), st.just(value=False)),
)


def _render(kind: str, version: str, *, guarded: bool) -> dict[str, object]:
    """Return a fixture step for one drawn step description."""
    if kind == "setup":
        return _setup(version, **({"if": "always()"} if guarded else {}))
    return _COVERAGE if kind == "coverage" else _RUN


def _naive_setup_sources(jobs: list[list[tuple[str, str, bool]]]) -> list[str]:
    """Return each call's setup source by the plainest possible reading."""
    expected: list[str] = []
    for steps in jobs:
        on_path = ""
        for kind, version, guarded in steps:
            match kind:
                case "setup":
                    on_path = "" if guarded else version
                case "coverage":
                    expected.append(on_path)
                case _:
                    pass
    return expected


@given(jobs=st.lists(st.lists(_STEP, max_size=8), min_size=1, max_size=4))
def test_the_setup_source_matches_a_naive_reading(
    tmp_path_factory: pytest.TempPathFactory,
    jobs: list[list[tuple[str, str, bool]]],
) -> None:
    """For any jobs, each call's setup source is its job's latest unguarded setup."""
    rendered = {
        f"job{index}": {"steps": [_render(k, v, guarded=g) for k, v, g in steps]}
        for index, steps in enumerate(jobs)
    }
    workflow = _write(tmp_path_factory.mktemp("property"), {"jobs": rendered})

    calls = _coverage_calls(workflow)

    assert [call.sources["setup-python"] for call in calls] == _naive_setup_sources(
        jobs
    ), "the reading must match the naive model"
