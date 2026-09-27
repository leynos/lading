"""Contract-test that both coverage lanes measure on one declared Python.

The pull-request lane ratchets against the baseline the push-to-main publisher
writes. The shared ``generate-coverage`` action chooses its interpreter in a
fixed order: its ``python-version`` input, then ``UV_PYTHON``, then the first
entry of ``.python-version``, then the ``python3`` the job put on ``PATH``,
which is the most recent ``actions/setup-python`` step before the call in its
job.

Every coverage call in both lanes must therefore declare at least one of those
sources, every source it declares must name the same version (a
higher-priority value silently overriding a lower one is how lanes drift), and
both lanes must measure on that one version. A setup step guarded by ``if:``
or allowed to fail with ``continue-on-error`` may not run, so it declares
nothing. The ratchet baseline key already carries the interpreter
(``ratchet-baseline-<os>-py<major.minor>-``), so a lane on another Python
misses its baseline rather than comparing against the wrong one; this
contract turns that silent restart into a failure.
"""

from __future__ import annotations

import itertools
import typing as typ

import pytest
import yaml

from tests.workflow_contracts.test_coverage_ownership import (
    CI_WORKFLOW_PATH,
    GENERATE_COVERAGE_ANY_REF,
    MAIN_COVERAGE_WORKFLOW_PATH,
    REPOSITORY_ROOT,
    YamlValue,
    _jobs,
    _load_workflow,
    _step_inputs,
    _steps,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

SETUP_PYTHON_ACTION = "actions/setup-python@"
#: The resolver's order, highest priority first.
SOURCES = ("input", "UV_PYTHON", ".python-version", "setup-python")
AGREE = "3.13"
CONFLICT = "3.14"

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


def _verdict(call: CoverageCall) -> str:
    """Return why a call's declared sources fail the contract, or empty."""
    declared = set(call.declared.values())
    if not declared:
        return "undeclared"
    return "conflicting" if len(declared) > 1 else ""


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
        elif GENERATE_COVERAGE_ANY_REF.match(action):
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


def _lane_calls() -> dict[str, list[CoverageCall]]:
    """Return both lanes' coverage calls, keyed by workflow file name."""
    python_version = _python_version_entry(REPOSITORY_ROOT / ".python-version")
    return {
        path.name: _coverage_calls(path, python_version)
        for path in (CI_WORKFLOW_PATH, MAIN_COVERAGE_WORKFLOW_PATH)
    }


def test_every_coverage_call_declares_one_python() -> None:
    """Both lanes call the action, and each call's declared sources agree."""
    lanes = _lane_calls()
    failures = {
        f"{lane}:{call.job}": _verdict(call)
        for lane, calls in lanes.items()
        for call in calls
        if _verdict(call)
    }

    assert all(lanes.values()), f"{list(lanes)} must each call the coverage action"
    assert failures == {}, f"these coverage calls fail the contract: {failures}"


def test_both_lanes_measure_on_one_python() -> None:
    """Every coverage call, in both lanes, measures on one version."""
    effective = {call.effective for calls in _lane_calls().values() for call in calls}

    assert len(effective) == 1, f"coverage lanes measure on {sorted(effective)}"


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
    ],
    ids=["before-the-call", "after-the-call", "another-job", "latest-setup-per-call"],
)
def test_each_call_reads_the_latest_setup_before_it_in_its_job(
    tmp_path: Path, jobs: dict[str, list[dict[str, object]]], expected: list[str]
) -> None:
    """A call's setup-python source is its own job's latest setup before it."""
    document = {"jobs": {name: {"steps": steps} for name, steps in jobs.items()}}
    calls = _coverage_calls(_write(tmp_path, document))

    assert [call.sources["setup-python"] for call in calls] == expected, (
        "a call reads the latest setup before it, in its own job"
    )


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        (({"UV_PYTHON": "3.12"}, {"UV_PYTHON": "3.13"}, {"UV_PYTHON": "3.14"}), "3.12"),
        (({}, {"UV_PYTHON": "3.13"}, {"UV_PYTHON": "3.14"}), "3.13"),
    ],
    ids=["step-over-job-and-workflow", "job-over-workflow"],
)
def test_the_innermost_uv_python_is_read(
    tmp_path: Path,
    scopes: tuple[dict[str, str], dict[str, str], dict[str, str]],
    expected: str,
) -> None:
    """``UV_PYTHON`` set in several scopes resolves to the innermost one."""
    step_env, job_env, workflow_env = scopes
    call = {**_COVERAGE, **({"env": step_env} if step_env else {})}
    document = {"env": workflow_env, "jobs": {"cov": {"env": job_env, "steps": [call]}}}
    (read,) = _coverage_calls(_write(tmp_path, document))

    assert read.sources["UV_PYTHON"] == expected, "the innermost UV_PYTHON wins"


class SourceCombination(typ.NamedTuple):
    """One combination of the sources the resolver reads for a single call."""

    input: str
    uv_scope: str
    uv_version: str
    python_version: str
    setup: str

    def document(self) -> dict[str, object]:
        """Return the fixture workflow declaring exactly these sources."""
        setup = {
            "named": _setup(AGREE),
            "unversioned": dict(_SETUP),
            "if": _setup(AGREE, **{"if": "false"}),
            "continue-on-error": _setup(AGREE, **{"continue-on-error": True}),
        }[self.setup]
        call: dict[str, object] = dict(_COVERAGE)
        if self.input:
            call["with"] = {"python-version": self.input}
        uv = {"UV_PYTHON": self.uv_version}
        if self.uv_scope == "step":
            call["env"] = uv
        job: dict[str, object] = {"steps": [setup, call]}
        if self.uv_scope == "job":
            job["env"] = uv
        document: dict[str, object] = {"jobs": {"cov": job}}
        if self.uv_scope == "workflow":
            document["env"] = uv
        return document

    def expected_declared(self) -> dict[str, str]:
        """Return the sources this combination declares, highest priority first."""
        named = {
            "input": self.input,
            "UV_PYTHON": self.uv_version if self.uv_scope else "",
            ".python-version": self.python_version,
            "setup-python": AGREE if self.setup == "named" else "",
        }
        return {name: version for name, version in named.items() if version}


_UV = [("", "")] + [
    (scope, version)
    for scope in ("step", "job", "workflow")
    for version in (AGREE, CONFLICT)
]
COMBINATIONS = [
    SourceCombination(given, uv_scope, uv_version, python_version, setup)
    for given, (uv_scope, uv_version), python_version, setup in itertools.product(
        ("", AGREE, CONFLICT),
        _UV,
        ("", AGREE, CONFLICT),
        ("named", "unversioned", "if", "continue-on-error"),
    )
]


@pytest.mark.parametrize("combination", COMBINATIONS, ids=str)
def test_every_source_combination_is_read_and_judged(
    tmp_path: Path, combination: SourceCombination
) -> None:
    """Exhaustively: each declared source is read, and disagreement or absence fails."""
    (call,) = _coverage_calls(
        _write(tmp_path, combination.document()), combination.python_version
    )
    declared = combination.expected_declared()
    versions = set(declared.values())

    assert call.declared == declared, "every declared source is read"
    assert call.effective == next(iter(declared.values()), ""), (
        "the effective version is the highest-priority declared source"
    )
    assert _verdict(call) == (
        "undeclared" if not versions else "conflicting" if len(versions) > 1 else ""
    ), "absence and disagreement fail; agreement passes"
