"""Contract-test main-owned CodeScene coverage publication workflows."""

from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
MAIN_COVERAGE_WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "coverage-main.yml"
)
GENERATE_COVERAGE_ACTION = (
    "leynos/shared-actions/.github/actions/generate-coverage"
    "@ac272c8273c5baa53a26b4ac96b8ede3e86b7f94"
)
UPLOAD_CODESCENE_ACTION = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage"
)


def _load_workflow(path: Path) -> dict[str, object]:
    """Return one decoded workflow mapping.

    Returns
    -------
    dict[str, object]
        The decoded workflow document.
    """
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path} must contain a mapping"
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    """Return a named workflow job.

    Returns
    -------
    dict[str, object]
        The named job declaration.
    """
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow must declare a jobs mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"workflow must declare the {name!r} job"
    return job


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    """Return a named job step.

    Returns
    -------
    dict[str, object]
        The named step declaration.
    """
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare a steps list"
    step = next(
        (
            candidate
            for candidate in steps
            if isinstance(candidate, dict) and candidate.get("name") == name
        ),
        None,
    )
    assert isinstance(step, dict), f"job must declare a {name!r} step"
    return step


def _uses(step: dict[str, object]) -> str:
    """Return a validated action reference.

    Returns
    -------
    str
        The step action reference.
    """
    uses = step.get("uses")
    assert isinstance(uses, str), f"step uses must be a string, got {uses!r}"
    return uses


def test_pull_request_coverage_uses_the_local_ratchet_only() -> None:
    """Keep pull-request coverage local and independent of CodeScene."""
    workflow = _load_workflow(CI_WORKFLOW_PATH)
    triggers = workflow.get("on")
    assert isinstance(triggers, dict), "ci.yml must declare a trigger mapping"
    assert "pull_request" in triggers, "ci.yml must run on pull requests"
    environment = workflow.get("env", {})
    assert isinstance(environment, dict), "ci.yml env must be a mapping"
    assert "CS_ACCESS_TOKEN" not in environment, (
        "ci.yml must not expose the CodeScene access token"
    )

    lint_test = _job(workflow, "lint-test")
    job_environment = lint_test.get("env", {})
    assert isinstance(job_environment, dict), "lint-test.env must be a mapping"
    assert "CS_ACCESS_TOKEN" not in job_environment, (
        "lint-test must not expose the CodeScene access token"
    )

    checkout = _step(lint_test, "Check out repository")
    checkout_inputs = checkout.get("with", {})
    assert isinstance(checkout_inputs, dict), "checkout.with must be a mapping"
    assert checkout_inputs.get("fetch-depth") != 0, (
        "pull-request coverage must not fetch the full Git history"
    )

    coverage = _step(lint_test, "Generate coverage")
    assert _uses(coverage) == GENERATE_COVERAGE_ACTION, (
        "pull-request coverage must use the shared generator"
    )
    assert coverage.get("if") == "github.event_name == 'pull_request'", (
        "coverage generation must be limited to pull requests"
    )
    coverage_inputs = coverage.get("with")
    assert isinstance(coverage_inputs, dict), "coverage.with must be a mapping"
    assert coverage_inputs.get("language") == "python", (
        "pull-request coverage must target Python"
    )
    assert coverage_inputs.get("python-source") == "./lading", (
        "pull-request coverage must preserve the lading source scope"
    )
    assert "pytest-workers" in coverage_inputs, (
        "pull-request coverage must configure pytest workers"
    )
    assert not coverage_inputs["pytest-workers"], (
        "pull-request coverage must run pytest serially"
    )
    assert coverage_inputs.get("with-ratchet") == "true", (
        "pull-request coverage must use the local ratchet"
    )

    steps = lint_test.get("steps")
    assert isinstance(steps, list), "lint-test must declare a steps list"
    assert all(
        UPLOAD_CODESCENE_ACTION not in _uses(step)
        for step in steps
        if isinstance(step, dict) and "uses" in step
    ), "pull-request CI must not upload coverage to CodeScene"
    assert all(
        "CS_ACCESS_TOKEN" not in step.get("env", {})
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("env", {}), dict)
    ), "pull-request steps must not expose the CodeScene access token"
    assert "codescene.io" not in CI_WORKFLOW_PATH.read_text(encoding="utf-8"), (
        "pull-request CI must not mention CodeScene"
    )


def test_main_coverage_writes_the_ratchet_and_uploads() -> None:
    """Keep ratchet publication and CodeScene upload on pushes to main only."""
    workflow = _load_workflow(MAIN_COVERAGE_WORKFLOW_PATH)
    assert workflow.get("on") == {"push": {"branches": ["main"]}}, (
        "main coverage must run only on pushes to main"
    )

    coverage_upload = _job(workflow, "coverage-upload")
    coverage = _step(coverage_upload, "Generate coverage")
    assert _uses(coverage) == GENERATE_COVERAGE_ACTION, (
        "main coverage must use the shared generator"
    )
    coverage_inputs = coverage.get("with")
    assert isinstance(coverage_inputs, dict), "coverage.with must be a mapping"
    assert coverage_inputs.get("language") == "python", (
        "main coverage must target Python"
    )
    assert coverage_inputs.get("python-source") == "./lading", (
        "main coverage must preserve the lading source scope"
    )
    assert "pytest-workers" in coverage_inputs, (
        "main coverage must configure pytest workers"
    )
    assert not coverage_inputs["pytest-workers"], (
        "main coverage must run pytest serially"
    )
    assert coverage_inputs.get("with-ratchet") == "true", (
        "main coverage must use the local ratchet"
    )

    upload = _step(coverage_upload, "Upload coverage data to CodeScene")
    assert _uses(upload).startswith(UPLOAD_CODESCENE_ACTION), (
        "main coverage must use the shared CodeScene uploader"
    )
    upload_inputs = upload.get("with")
    assert isinstance(upload_inputs, dict), "upload.with must be a mapping"
    assert upload_inputs.get("mode") == "upload", (
        "main coverage must publish its report to CodeScene"
    )
