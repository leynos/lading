"""Contract tests for how this repository generates coverage.

lading now generates coverage with the shared ``generate-coverage`` action on
both of its coverage lanes, completing the adoption this repository had to
defer. The deferral existed because the action's manifest-based detection
treats a repository-root ``Cargo.toml`` as Rust, and lading commits one as a
fixture for its own workspace-release scenarios (see
``docs/repository-layout.md``); it names ``crates/`` directories that do not
exist on disk, so detection would run ``cargo llvm-cov nextest --workspace``
against them and fail. ``language: python`` skips that path, and
``python-source`` holds the metric to the package instead of widening it to
tests and fixtures, which is what the bespoke slipcover invocation did.

The completed shape these tests pin:

- no workflow invokes slipcover, coverage.py or pytest-forked by hand; every
  coverage report comes from the shared action;
- every call to that action is pinned to a full commit SHA, forces
  ``language: python``, scopes the metric with ``python-source`` and enables
  the ratchet;
- the lane that serves pull requests declines the action's own artefact, and
  the push-to-main publisher keeps it, because that published report is what
  the CodeScene upload step reads.

Workflows are enumerated from ``.github/workflows`` and classified by their
``on:`` triggers rather than named, so a coverage lane added later is covered
the day it appears. CodeScene ownership -- which workflow may hold the token
and call ``cs-coverage`` -- is a separate contract.
"""

from __future__ import annotations

import collections.abc as cabc
import re
import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

pytestmark = pytest.mark.skipif(
    not WORKFLOWS_DIR.is_dir(),
    reason=(
        "workflow directory not present in this working copy (for example "
        "inside mutmut's mutants/ sandbox, which does not copy .github/)"
    ),
)

GENERATE_COVERAGE_RE = re.compile(
    r"^leynos/shared-actions/\.github/actions/generate-coverage@(?P<sha>[0-9a-f]{40})$"
)

#: A ``uses:`` value naming the shared action at any reference. Matching the
#: unpinned form too is deliberate: a step that moved to a branch must be
#: recognised as a coverage step and then fail the pin assertion, rather than
#: vanishing from the set and passing every test by absence.
GENERATE_COVERAGE_ANY_REF_RE = re.compile(
    r"^leynos/shared-actions/\.github/actions/generate-coverage@"
)

#: Commands that would generate a coverage report outside the shared action.
#: `--forked` is included because the invocation this adoption replaced used
#: pytest-forked, and its return would mean the bespoke lane had come back.
HAND_ROLLED_COVERAGE_TOKENS = ("slipcover", "pytest-forked", "--forked", "coverage xml")


class CoverageStep(typ.NamedTuple):
    """One shared-action coverage step and where it was found."""

    workflow: str
    job: str
    name: str
    uses: str
    inputs: dict[str, object]


def _load(path: Path) -> dict[str, object]:
    """Parse one workflow file."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} must parse to a mapping"
    return typ.cast("dict[str, object]", loaded)


def _workflows() -> dict[str, dict[str, object]]:
    """Return every workflow in the repository, keyed by file name."""
    paths = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    assert paths, "no workflows found to check"
    return {path.name: _load(path) for path in paths}


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    """Return the ``on:`` mapping.

    PyYAML parses an unquoted ``on`` key as the boolean ``True``, so both
    spellings are read; a workflow using the quoted form would otherwise be
    treated as having no triggers at all and silently drop out of every
    classification below.

    Returns
    -------
    dict[str, object]
        The declared triggers, with a bare string or list of trigger names
        normalised to a mapping.

    Raises
    ------
    AssertionError
        If the workflow declares no triggers at all.
    """
    match workflow.get("on", workflow.get(True)):
        case str() as trigger:
            return {trigger: None}
        case list() as names:
            return dict.fromkeys(names)
        case dict() as triggers:
            return typ.cast("dict[str, object]", triggers)
        case other:
            message = f"a workflow must declare its triggers, got {other!r}"
            raise AssertionError(message)


def _jobs(workflow: dict[str, object]) -> dict[str, object]:
    """Return the workflow's jobs mapping."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "a workflow must declare a jobs mapping"
    return typ.cast("dict[str, object]", jobs)


def _steps(
    workflow: dict[str, object],
) -> cabc.Iterator[tuple[str, dict[str, object]]]:
    """Yield every ``(job name, step)`` pair in the workflow."""
    for job_name, job in _jobs(workflow).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict):
                yield str(job_name), typ.cast("dict[str, object]", step)


def _coverage_steps() -> list[CoverageStep]:
    """Return every shared-action coverage step across all workflows."""
    found: list[CoverageStep] = []
    for name, workflow in _workflows().items():
        for job_name, step in _steps(workflow):
            uses = step.get("uses")
            if isinstance(uses, str) and GENERATE_COVERAGE_ANY_REF_RE.match(uses):
                inputs = step.get("with") or {}
                assert isinstance(inputs, dict), (
                    f"{name}: the coverage step must pass inputs as a mapping"
                )
                found.append(
                    CoverageStep(
                        workflow=name,
                        job=job_name,
                        name=str(step.get("name") or job_name),
                        uses=uses,
                        inputs=typ.cast("dict[str, object]", inputs),
                    )
                )
    return found


def _serves_pull_requests(workflow: dict[str, object]) -> bool:
    """Report whether the workflow can run for a pull request."""
    triggers = _triggers(workflow)
    return any(
        trigger in triggers for trigger in ("pull_request", "pull_request_target")
    )


def _is_publisher(name: str) -> bool:
    """Report whether a workflow publishes coverage from trunk.

    The predicate is "pushes to main **and** serves no pull request". A
    repository's ``ci.yml`` usually declares both triggers, and the looser
    reading would make one file required to publish and forbidden from
    publishing at the same time.

    Returns
    -------
    bool
        True when the workflow pushes to main and serves no pull request.
    """
    workflow = _workflows()[name]
    if _serves_pull_requests(workflow):
        return False
    push = _triggers(workflow).get("push")
    branches = push.get("branches") if isinstance(push, dict) else None
    return isinstance(branches, list) and "main" in branches


def test_no_workflow_generates_coverage_by_hand() -> None:
    """Coverage comes from the shared action, never from a hand-rolled run."""
    offenders: list[str] = []
    for name, workflow in _workflows().items():
        for job_name, step in _steps(workflow):
            script = step.get("run")
            if not isinstance(script, str):
                continue
            offenders.extend(
                f"{name}:{job_name}:{token}"
                for token in HAND_ROLLED_COVERAGE_TOKENS
                if token in script
            )
    assert not offenders, (
        "these run: steps generate coverage outside the shared action, which "
        "is what the adoption removed: " + ", ".join(sorted(offenders))
    )


def test_both_coverage_lanes_use_the_shared_action() -> None:
    """A lane for pull requests and a publisher each generate coverage.

    The presence half of the check above: a contract that only forbids the
    hand-rolled invocation is satisfied by deleting coverage altogether.
    """
    steps = _coverage_steps()
    assert steps, "no workflow generates coverage through the shared action"
    pull_request_lanes = {
        step.workflow
        for step in steps
        if _serves_pull_requests(_workflows()[step.workflow])
    }
    publishers = {step.workflow for step in steps if _is_publisher(step.workflow)}
    assert pull_request_lanes, (
        "no pull-request workflow generates coverage, so nothing measures a "
        "change before it merges"
    )
    assert publishers, (
        "no push-to-main workflow generates coverage, so the ratchet baseline "
        "and the CodeScene upload would have no report"
    )


def test_every_coverage_step_is_pinned_to_a_commit_sha() -> None:
    """Each call names the shared action at a full 40-hex commit SHA."""
    for step in _coverage_steps():
        assert GENERATE_COVERAGE_RE.match(step.uses), (
            f"{step.workflow}: the {step.name!r} step must pin "
            "generate-coverage to a full 40-hex commit SHA, got "
            f"{step.uses!r}"
        )


def test_every_coverage_step_forces_the_python_language_scope() -> None:
    """Detection is overridden, so the fixture Cargo.toml is never resolved."""
    for step in _coverage_steps():
        assert step.inputs.get("language") == "python", (
            f"{step.workflow}: the {step.name!r} step must pass "
            "language: python; without it the action's detection treats the "
            "fixture root Cargo.toml as a buildable crate. Got "
            f"{step.inputs.get('language')!r}"
        )


def test_every_coverage_step_scopes_the_metric_to_the_package() -> None:
    """The measured population stays the package, as it was before adoption."""
    for step in _coverage_steps():
        assert step.inputs.get("python-source") == "./lading", (
            f"{step.workflow}: the {step.name!r} step must pass "
            "python-source: ./lading, or the metric widens to tests and "
            f"fixtures. Got {step.inputs.get('python-source')!r}"
        )


def test_every_coverage_step_enables_the_ratchet() -> None:
    """Both lanes compare against the baseline main publishes."""
    for step in _coverage_steps():
        assert step.inputs.get("with-ratchet") in {True, "true"}, (
            f"{step.workflow}: the {step.name!r} step must enable the "
            f"ratchet, got {step.inputs.get('with-ratchet')!r}"
        )


def test_only_the_publisher_archives_the_action_report() -> None:
    """The pull-request lane declines the archive; the publisher keeps it.

    The pull-request lane publishes its report through a step of its own, and
    the action's archive would collide with it. The publisher's archive is the
    report the CodeScene upload reads, so suppressing it there would leave the
    upload with nothing.
    """
    for step in _coverage_steps():
        publish = step.inputs.get("publish-artefact")
        if _serves_pull_requests(_workflows()[step.workflow]):
            assert publish in {False, "false"}, (
                f"{step.workflow}: the {step.name!r} step serves pull "
                "requests and must pass publish-artefact: 'false', got "
                f"{publish!r}"
            )
        elif _is_publisher(step.workflow):
            assert publish is None, (
                f"{step.workflow}: the {step.name!r} step is the publisher "
                "and must leave publish-artefact unset so the action archives "
                f"the report the upload reads, got {publish!r}"
            )
