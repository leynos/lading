"""Contract-test main-owned CodeScene coverage and the Markdown lint wiring.

Two rules meet here. Main owns CodeScene publication, so no pull-request
workflow may carry the token, name the service or invoke its command; the
push-to-main publisher is the only uploader. And Markdown is linted in CI only
through the upstream action, pinned to a commit.

The generate-coverage half of the coverage rule is deliberately absent. It
needs leynos/shared-actions#502, which carries the `python-source` scope and
the scripts-directory PATH fix; until that lands, both workflows keep the
repository's own slipcover invocation, and a contract demanding the shared
action would be asserting something this repository cannot yet be.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIRECTORY = REPOSITORY_ROOT / ".github" / "workflows"
CI_WORKFLOW_PATH = WORKFLOW_DIRECTORY / "ci.yml"
MAIN_COVERAGE_WORKFLOW_PATH = WORKFLOW_DIRECTORY / "coverage-main.yml"

UPLOAD_CODESCENE_ACTION = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage"
)
#: The uploader at the commit that resolves cs-coverage from a committed
#: manifest rather than the latest release. Asserted whole rather than by
#: prefix: a pin that drifted to a branch or a tag would still start with the
#: same path.
UPLOAD_CODESCENE_PIN = (
    f"{UPLOAD_CODESCENE_ACTION}@a5765019912a8ab6882b12db049c7cde635f3a85"
)
#: The upstream Markdown linter, pinned to the commit `v24.2.0` points at
#: rather than to the annotated tag object of the same name.
MARKDOWNLINT_ACTION = (
    "DavidAnson/markdownlint-cli2-action@21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"
)


def _load_workflow(path: Path) -> dict[str, typ.Any]:
    """Return one decoded workflow mapping.

    Returns
    -------
    dict
        The decoded workflow document, with the `on:` key restored. PyYAML
        reads a bare `on` as the boolean True, so it is moved back before any
        caller looks for a trigger that would otherwise appear absent.
    """
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path} must contain a mapping"
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def _job(workflow: dict[str, typ.Any], name: str) -> dict[str, typ.Any]:
    """Return a named workflow job.

    Returns
    -------
    dict
        The named job declaration.
    """
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow must declare a jobs mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"workflow must declare the {name!r} job"
    return job


def _step(job: dict[str, typ.Any], name: str) -> dict[str, typ.Any]:
    """Return a named job step.

    Returns
    -------
    dict
        The named step declaration.
    """
    steps = _steps(job)
    step = next(
        (candidate for candidate in steps if candidate.get("name") == name),
        None,
    )
    assert isinstance(step, dict), f"job must declare a {name!r} step"
    return step


def _steps(job: dict[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return a job's steps.

    Returns
    -------
    list of dict
        Every mapping in the job's step list.
    """
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare a steps list"
    return [step for step in steps if isinstance(step, dict)]


def _uses(step: dict[str, typ.Any]) -> str:
    """Return a validated action reference.

    Returns
    -------
    str
        The step action reference.
    """
    uses = step.get("uses")
    assert isinstance(uses, str), f"step uses must be a string, got {uses!r}"
    return uses


def _pull_request_workflows() -> list[Path]:
    """Return every workflow that runs on a pull request.

    Enumerated rather than named, so a workflow added later is covered the
    day it appears instead of the day somebody remembers to list it.

    Returns
    -------
    list of Path
        The workflow files whose triggers include `pull_request`.
    """
    found: list[Path] = []
    for path in sorted(WORKFLOW_DIRECTORY.glob("*.yml")):
        triggers = _load_workflow(path).get("on")
        names = triggers if isinstance(triggers, dict | list) else [triggers]
        if "pull_request" in names:
            found.append(path)
    assert found, "no workflow runs on pull requests, so this proves nothing"
    return found


def test_no_pull_request_workflow_touches_codescene() -> None:
    """Keep CodeScene off every pull-request lane, by any of its three doors.

    The token, the service and the command are checked separately because
    each alone reintroduces the failure this rule exists to prevent: a lane
    holding the credential can upload, a lane naming the project can reach
    it, and a lane running `cs-coverage` fails on whatever CLI the runner
    resolved. Every pull-request workflow is enumerated rather than named.
    """
    for path in _pull_request_workflows():
        text = path.read_text(encoding="utf-8")
        workflow = _load_workflow(path)

        assert "CS_ACCESS_TOKEN" not in text, (
            f"{path.name} must not carry the CodeScene access token"
        )
        assert "codescene.io" not in text, (
            f"{path.name} must not name a CodeScene endpoint"
        )
        assert "cs-coverage" not in text, (
            f"{path.name} must not invoke the CodeScene CLI"
        )

        jobs = workflow.get("jobs")
        assert isinstance(jobs, dict), f"{path.name} must declare jobs"
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            for step in _steps(job):
                assert UPLOAD_CODESCENE_ACTION not in str(step.get("uses", "")), (
                    f"{path.name}:{job_name} must not use the CodeScene action"
                )


def test_the_pull_request_lane_does_not_fetch_full_history() -> None:
    """Drop the full-history checkout the removed gate needed.

    `fetch-depth: 0` was there so `cs-coverage check` could reach the merge
    base. With the gate gone it is a cost with no purchaser, and leaving it
    would be the visible trace of a lane that still thought it published.
    """
    checkout = _step(
        _job(_load_workflow(CI_WORKFLOW_PATH), "lint-test"), "Check out repository"
    )
    assert checkout.get("with", {}).get("fetch-depth") != 0, (
        "the pull-request lane must not fetch the full Git history"
    )


def test_main_is_the_only_uploader_and_is_pinned() -> None:
    """Publish from pushes to main alone, through a commit-pinned uploader.

    Three claims, each failing on its own. The trigger, because a workflow
    that also ran on pull requests would restore what the first case forbids
    by another route. The mode, because `check` is the call that failed. And
    the pin, because an unpinned `cs-coverage` is what broke Cobertura
    parsing across the estate, and this step is now the only place left to
    fix it. The checksum input is asserted absent by its old name, which the
    action rejects when it carries a value.
    """
    workflow = _load_workflow(MAIN_COVERAGE_WORKFLOW_PATH)
    assert workflow.get("on") == {"push": {"branches": ["main"]}}, (
        "coverage publication must run only on pushes to main"
    )

    upload = _step(
        _job(workflow, "coverage-upload"), "Upload coverage data to CodeScene"
    )
    assert _uses(upload) == UPLOAD_CODESCENE_PIN, (
        "the uploader must be pinned to the manifest-resolving commit"
    )
    upload_inputs = upload.get("with")
    assert isinstance(upload_inputs, dict), "upload.with must be a mapping"
    assert upload_inputs.get("mode") == "upload", (
        "the publisher must upload rather than check"
    )
    assert "installer-checksum" not in upload_inputs, (
        "installer-checksum is rejected when non-empty; the manifest pins the CLI"
    )


def test_markdown_is_linted_only_through_the_pinned_action() -> None:
    """Lint Markdown in CI through the upstream action and nothing else.

    Two claims, and each fails on its own. The action must be pinned to a
    commit, since the tag object of the same name is immutable but is not a
    commit and names a different kind of thing. And no step may invoke the
    linter itself: a `run:` line reaching for `markdownlint-cli2` resolves
    whatever version the runner happens to have, which is the drift the pin
    exists to stop, and it would sit beside the action rather than replace it
    where nothing was looking.
    """
    lint_test = _job(_load_workflow(CI_WORKFLOW_PATH), "lint-test")

    markdown = _step(lint_test, "Lint Markdown")
    assert _uses(markdown) == MARKDOWNLINT_ACTION, (
        "Markdown must be linted through the action at its commit pin"
    )
    markdown_inputs = markdown.get("with")
    assert isinstance(markdown_inputs, dict), "the lint step must declare inputs"
    assert markdown_inputs.get("globs") == "**/*.md", (
        "the lint step must cover every Markdown file"
    )

    invocations = [
        step.get("name")
        for step in _steps(lint_test)
        if "markdownlint" in str(step.get("run", ""))
    ]
    assert not invocations, (
        f"CI must not invoke the Markdown linter from a run step: {invocations}"
    )
