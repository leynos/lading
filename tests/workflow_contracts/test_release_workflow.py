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
UPLOAD_COMMAND = ("uv", "run", "--script", UPLOAD_SCRIPT)
RELEASE_ACTION = "softprops/action-gh-release"
PUBLISH_COMMAND = ("gh", "release", "edit")
PURE_WHEEL_ACTION = "./.github/actions/pure-python-wheel"
UPLOAD_STEP_NAME = "Upload wheels to release"
PUBLISH_STEP_NAME = "Publish release"
SHELL_CONTROL_OPERATOR_CHARACTERS = frozenset("&;|")
# The CLI's default is declared by scripts/upload_release_wheels.py.
DEFAULT_UPLOAD_DIRECTORY = "dist"


class WorkflowStep(typ.TypedDict, total=False):
    """One decoded step of a workflow job."""

    name: str
    uses: str
    run: str
    env: dict[str, str]
    with_: dict[str, str]


def _load_job(job_name: str) -> dict[str, object]:
    """Return a named workflow job, validating the decoded mappings."""
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        message = f"{RELEASE_WORKFLOW} must decode to a mapping"
        raise TypeError(message)
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        message = f"{RELEASE_WORKFLOW} must define a jobs mapping"
        raise TypeError(message)
    job = jobs.get(job_name)
    if not isinstance(job, dict):
        message = f"job {job_name!r} must decode to a mapping"
        raise TypeError(message)
    return typ.cast("dict[str, object]", job)


def _release_job() -> dict[str, object]:
    """Return the workflow's ``release`` job."""
    return _load_job("release")


def _as_step(step: object) -> WorkflowStep:
    """Rename the workflow's ``with`` key for use as a typed step."""
    if not isinstance(step, dict):
        message = f"every workflow step must be a mapping, got {step!r}"
        raise TypeError(message)
    renamed = {
        ("with_" if key == "with" else key): value for key, value in step.items()
    }
    return typ.cast("WorkflowStep", renamed)


def _job_steps(job_name: str) -> list[WorkflowStep]:
    """Return the normalized steps of a named workflow job."""
    steps = _load_job(job_name).get("steps")
    if not isinstance(steps, list):
        message = f"the {job_name} job must define a list of steps"
        raise TypeError(message)
    return [_as_step(step) for step in steps]


def _release_steps() -> list[WorkflowStep]:
    """Return the steps of the workflow's ``release`` job."""
    return _job_steps("release")


def _pure_wheel_steps() -> list[WorkflowStep]:
    """Return the steps of the workflow's ``pure-wheel`` job."""
    return _job_steps("pure-wheel")


def _download_steps() -> list[WorkflowStep]:
    """Return the steps that download workflow artefacts."""
    return [
        step
        for step in _release_steps()
        if step.get("uses", "").startswith("actions/download-artifact")
    ]


def _wheel_producer_steps() -> list[WorkflowStep]:
    """Return the steps that build the pure Python wheel artefact."""
    steps = _pure_wheel_steps()
    return [step for step in steps if step.get("uses") == PURE_WHEEL_ACTION]


def _upload_steps() -> list[WorkflowStep]:
    """Return the steps that run the upload script."""
    return [step for step in _release_steps() if step.get("name") == UPLOAD_STEP_NAME]


def _shell_tokens(command: str) -> list[str]:
    """Split shell words and control operators while respecting comments."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _shell_control_operators(command: str) -> set[str]:
    """Return control operators and multiple command lines in a shell step."""
    operators = {
        token
        for token in _shell_tokens(command)
        if token and set(token) <= SHELL_CONTROL_OPERATOR_CHARACTERS
    }
    command_lines = []
    pending_line = ""
    for line in command.splitlines():
        pending_line = f"{pending_line}\n{line}" if pending_line else line
        try:
            tokens = shlex.split(pending_line, comments=True)
        except ValueError:
            continue
        if tokens:
            command_lines.append(tokens)
        pending_line = ""
    if pending_line:
        operators.add("incomplete command")
    if len(command_lines) > 1:
        operators.add("multiple commands")
    return operators


def _upload_directory(tokens: list[str]) -> str:
    """Return the uploader directory option or its command-line default."""
    for index, argument in enumerate(tokens):
        if argument.startswith("--directory="):
            return argument.partition("=")[2]
        if argument == "--directory":
            assert index + 1 < len(tokens), "--directory must have a value"
            return tokens[index + 1]
    return DEFAULT_UPLOAD_DIRECTORY


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
        if tuple(_shell_tokens(step.get("run", "")))[:3] == PUBLISH_COMMAND
    ]


def _index_of(step: WorkflowStep) -> int:
    """Return the position of ``step`` within the release job's steps."""
    return _release_steps().index(step)


def test_the_pure_wheel_job_has_one_named_wheel_producer() -> None:
    """The producer action publishes the expected wheel artefact."""
    producers = _wheel_producer_steps()

    assert len(producers) == 1, f"expected exactly one pure-wheel producer: {producers}"
    artifact_name = producers[0].get("with_", {}).get("artifact-name")
    assert artifact_name == "wheels-pure", (
        f"the producer must name its artefact 'wheels-pure', got {artifact_name!r}"
    )


def test_the_release_download_names_the_producer_artefact() -> None:
    """The release downloads the artefact named by its wheel producer."""
    producers = _wheel_producer_steps()
    downloads = _download_steps()

    assert len(producers) == 1, f"expected exactly one pure-wheel producer: {producers}"
    assert len(downloads) == 1, f"expected exactly one artefact download: {downloads}"
    artifact_name = producers[0].get("with_", {}).get("artifact-name")
    download_name = downloads[0].get("with_", {}).get("name")

    assert download_name == artifact_name, (
        f"the download must name the producer artefact {artifact_name!r}, "
        f"got {download_name!r}"
    )


def test_the_release_job_depends_on_the_wheel_producer() -> None:
    """The release job cannot start before the pure wheel exists."""
    needs = _release_job().get("needs", [])
    dependencies = [needs] if isinstance(needs, str) else needs

    assert isinstance(dependencies, list), (
        f"release needs must be a string or list: {needs!r}"
    )
    assert "pure-wheel" in dependencies, (
        f"the release job must depend on pure-wheel, got {dependencies!r}"
    )


def test_the_download_path_is_the_uploader_directory() -> None:
    """The uploader searches the directory where the artefact is extracted."""
    downloads = _download_steps()
    upload_steps = _upload_steps()

    assert len(downloads) == 1, f"expected exactly one artefact download: {downloads}"
    assert len(upload_steps) == 1, f"expected exactly one upload step: {upload_steps}"
    download_path = downloads[0].get("with_", {}).get("path")
    assert download_path == "dist", (
        f"the artefact must download into dist, got {download_path!r}"
    )

    tokens = shlex.split(upload_steps[0].get("run", ""), comments=True)
    upload_directory = _upload_directory(tokens)

    assert upload_directory == download_path, (
        f"the uploader directory must match the download path {download_path!r}, "
        f"got {upload_directory!r}"
    )


def test_the_upload_runs_the_script_as_its_own_command() -> None:
    """The upload runs the script, which fails when no wheel is found."""
    upload_steps = _upload_steps()

    assert len(upload_steps) == 1, f"expected exactly one upload step: {upload_steps}"
    tokens = tuple(shlex.split(upload_steps[0]["run"], comments=True))
    assert tokens[: len(UPLOAD_COMMAND)] == UPLOAD_COMMAND, (
        f"the upload must run {' '.join(UPLOAD_COMMAND)}, got {tokens}"
    )
    operators = _shell_control_operators(upload_steps[0]["run"])
    assert not operators, (
        f"the upload command must fail when the uploader fails, got {operators}"
    )


@pytest.mark.parametrize("forbidden", ["xargs", "find"])
def test_the_upload_is_not_a_shell_pipeline(forbidden: str) -> None:
    """No release step may search for wheels through a shell pipeline.

    The exit status of a pipeline is its last command's, so ``set -eu`` cannot
    see the search fail. This assertion is what makes the regression loud.
    """
    offenders = [
        tokens
        for step in _release_steps()
        if (tokens := _shell_tokens(step.get("run", ""))) and forbidden in tokens
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


def test_the_release_is_drafted_before_download_and_upload() -> None:
    """A download or upload failure must leave the release as a draft."""
    creations = _creation_steps()
    downloads = _download_steps()
    uploads = _upload_steps()

    assert len(creations) == 1, f"expected one release-creation step: {creations}"
    assert len(downloads) == 1, f"expected one artefact download: {downloads}"
    assert len(uploads) == 1, f"expected one upload step: {uploads}"
    creation_index = _index_of(creations[0])

    assert creation_index < _index_of(downloads[0]), (
        "the draft must be created before downloading the wheel"
    )
    assert creation_index < _index_of(uploads[0]), (
        "the draft must be created before uploading the wheel"
    )


def test_release_publication_steps_do_not_continue_after_failure() -> None:
    """Critical release steps and the release job must fail closed."""
    critical_steps = [
        *_creation_steps(),
        *_download_steps(),
        *_upload_steps(),
        *_publish_steps(),
    ]

    for step in critical_steps:
        assert not step.get("continue-on-error"), (
            f"release step must not continue after failure: {step}"
        )
    assert not _release_job().get("continue-on-error"), (
        "the release job must not continue after a step failure"
    )


def test_publication_has_no_failure_override_condition() -> None:
    """Publication must retain the implicit success check after upload."""
    publishes = _publish_steps()

    assert len(publishes) == 1, f"expected one publish step: {publishes}"
    condition = str(publishes[0].get("if", ""))
    normalized_condition = condition.strip()
    if normalized_condition.startswith("${{") and normalized_condition.endswith("}}"):
        normalized_condition = normalized_condition[3:-2].strip()
    assert normalized_condition.lower() in {"", "success()"}, (
        f"publish condition must be absent or success-only: {condition!r}"
    )


def test_the_release_is_published_only_after_the_upload() -> None:
    """Publication is the last thing the job does.

    Ordering is the whole point: a publish step that ran before the upload
    would restore exactly the behaviour the draft is there to prevent.
    """
    publishes = _publish_steps()

    assert len(publishes) == 1, f"expected one publish step: {publishes}"
    assert publishes[0].get("name") == PUBLISH_STEP_NAME, (
        f"the publish command must use the {PUBLISH_STEP_NAME!r} step, "
        f"got {publishes[0]}"
    )
    tokens = tuple(shlex.split(publishes[0]["run"], comments=True))
    operators = _shell_control_operators(publishes[0]["run"])
    assert not operators, (
        f"the publish command must fail when gh release edit fails, got {operators}"
    )
    assert tokens[: len(PUBLISH_COMMAND)] == PUBLISH_COMMAND, (
        f"the publish step must run {' '.join(PUBLISH_COMMAND)}, got {tokens}"
    )
    release_target_index = len(PUBLISH_COMMAND)
    assert tokens[release_target_index : release_target_index + 1] == (
        "$GITHUB_REF_NAME",
    ), f"the publish command must target the pushed tag, got {tokens}"
    assert "--draft=false" in tokens, (
        f"the publish step must clear the draft flag, got {tokens}"
    )
    environment = publishes[0].get("env", {})
    assert environment.get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
        f"the publish command needs the workflow token: {environment}"
    )
    assert _index_of(publishes[0]) > _index_of(_upload_steps()[0]), (
        "the release must be published after the wheels are uploaded"
    )
