"""Contract tests for the release workflow's wheel upload.

The upload step used to be `find dist/wheels-* ... | xargs -0 -r gh release
upload`. A pipeline reports the exit status of its last command, so the step
passed while uploading nothing, and two releases published without their wheel.
These tests hold the two properties that prevented it: the artefact is named on
download, and the search and its empty case run in Python where a failure can
fail the step.

The assertions name exact values rather than accept any non-empty one: a
contract that passes for any artefact name would also pass for the wrong one.
"""

from __future__ import annotations

import shlex
import typing as typ
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
UPLOAD_SCRIPT = "scripts/upload_release_wheels.py"
WHEEL_ARTEFACT = "wheels-pure"
UPLOAD_COMMAND = ("uv", "run", "--script", UPLOAD_SCRIPT)
RELEASE_ACTION = "softprops/action-gh-release"
PUBLISH_COMMAND = ("gh", "release", "edit")


class WorkflowStep(typ.TypedDict, total=False):
    """One decoded step of a workflow job."""

    name: str
    uses: str
    run: str
    env: dict[str, str]
    with_: dict[str, str]


def _release_job() -> dict[str, typ.Any]:
    """Return the workflow's ``release`` job.

    Returns
    -------
    dict[str, typ.Any]
        The decoded job mapping.

    Raises
    ------
    TypeError
        If the workflow does not decode to a mapping.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        message = f"{RELEASE_WORKFLOW} must decode to a mapping"
        raise TypeError(message)
    return workflow.get("jobs", {}).get("release", {})


def _as_step(step: object) -> WorkflowStep:
    """Return ``step`` with ``with`` renamed, since it is a Python keyword.

    Returns
    -------
    WorkflowStep
        The renamed step mapping.

    Raises
    ------
    TypeError
        If the step is not a mapping.
    """
    if not isinstance(step, dict):
        message = f"every release step must be a mapping, got {step!r}"
        raise TypeError(message)
    renamed = {
        ("with_" if key == "with" else key): value for key, value in step.items()
    }
    return typ.cast("WorkflowStep", renamed)


def _release_steps() -> list[WorkflowStep]:
    """Return the steps of the workflow's ``release`` job.

    Returns
    -------
    list[WorkflowStep]
        The decoded steps.

    Raises
    ------
    TypeError
        If the job does not define a list of steps.
    """
    steps = _release_job().get("steps")
    if not isinstance(steps, list):
        message = "the release job must define a list of steps"
        raise TypeError(message)
    return [_as_step(step) for step in steps]


def _upload_steps() -> list[WorkflowStep]:
    """Return the steps that run the upload script."""
    return [step for step in _release_steps() if UPLOAD_SCRIPT in step.get("run", "")]


def _creation_steps() -> list[WorkflowStep]:
    """Return the steps that create the GitHub release."""
    return [
        step
        for step in _release_steps()
        if step.get("uses", "").startswith(RELEASE_ACTION)
    ]


def _publish_steps() -> list[WorkflowStep]:
    """Return the steps that take the release out of draft."""
    return [
        step
        for step in _release_steps()
        if tuple(shlex.split(step.get("run", "")))[:3] == PUBLISH_COMMAND
    ]


def _index_of(step: WorkflowStep) -> int:
    """Return the position of ``step`` within the release job's steps."""
    return _release_steps().index(step)


def test_the_wheel_artefact_is_named_on_download() -> None:
    """The download names the wheel artefact instead of trusting a layout.

    Without the name, the action's directory layout decides where the wheel
    lands, and a search written for one layout silently matches nothing under
    another.
    """
    downloads = [
        step
        for step in _release_steps()
        if step.get("uses", "").startswith("actions/download-artifact")
    ]

    assert downloads, "the release job must download the built wheel"
    names = [step.get("with_", {}).get("name") for step in downloads]
    assert names == [WHEEL_ARTEFACT], (
        f"the download must name {WHEEL_ARTEFACT!r}, got {names}"
    )


def test_the_upload_runs_the_script_as_its_own_command() -> None:
    """The upload runs the script, which fails when no wheel is found."""
    upload_steps = _upload_steps()

    assert len(upload_steps) == 1, f"expected exactly one upload step: {upload_steps}"
    tokens = tuple(shlex.split(upload_steps[0]["run"]))
    assert tokens[: len(UPLOAD_COMMAND)] == UPLOAD_COMMAND, (
        f"the upload must run {' '.join(UPLOAD_COMMAND)}, got {tokens}"
    )


def test_an_explicit_upload_directory_is_the_download_target() -> None:
    """If the step names a directory, it is the one the artefact lands in.

    The script defaults to ``dist``, so the option is optional; naming a
    different directory would be a silent mismatch with the download step.
    """
    tokens = tuple(shlex.split(_upload_steps()[0]["run"]))
    if "--directory" not in tokens:
        return

    value = tokens[tokens.index("--directory") + 1]
    assert value == "dist", f"the upload directory must be dist, got {value!r}"


@pytest.mark.parametrize("forbidden", ["xargs", "find dist"])
def test_the_upload_is_not_a_shell_pipeline(forbidden: str) -> None:
    """No release step may search for wheels through a shell pipeline.

    The exit status of a pipeline is its last command's, so ``set -eu`` cannot
    see the search fail. This assertion is what makes the regression loud.
    """
    offenders = [
        step.get("run", "")
        for step in _release_steps()
        if forbidden in step.get("run", "")
    ]

    assert not offenders, (
        f"release steps must not use {forbidden!r} to find wheels: {offenders}"
    )


def test_the_upload_step_receives_the_tag_and_a_token() -> None:
    """The script needs the tag it uploads to and credentials to do it."""
    environment = _upload_steps()[0].get("env", {})

    assert environment.get("GITHUB_REF_NAME") == "${{ github.ref_name }}", (
        f"the script reads the tag from the ref name: {environment}"
    )
    assert environment.get("GITHUB_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
        f"gh release upload needs the workflow token: {environment}"
    )


def test_the_release_is_created_as_a_draft() -> None:
    """The release is a draft until its wheel is attached.

    The action publishes by default, so a failure between creation and upload
    would leave a visible release with nothing on it -- which is what v0.3.0
    and v0.3.1 were.
    """
    creations = _creation_steps()

    assert len(creations) == 1, f"expected one release-creation step: {creations}"
    assert creations[0].get("with_", {}).get("draft") is True, (
        f"the release must be created with draft: true, got {creations[0]}"
    )


def test_the_release_is_published_only_after_the_upload() -> None:
    """Publication is the last thing the job does.

    Ordering is the whole point: a publish step that ran before the upload
    would restore exactly the behaviour the draft is there to prevent.
    """
    publishes = _publish_steps()

    assert len(publishes) == 1, f"expected one publish step: {publishes}"
    tokens = tuple(shlex.split(publishes[0]["run"]))
    assert "--draft=false" in tokens, (
        f"the publish step must clear the draft flag, got {tokens}"
    )
    assert _index_of(publishes[0]) > _index_of(_upload_steps()[0]), (
        "the release must be published after the wheels are uploaded"
    )
