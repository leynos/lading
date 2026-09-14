"""Contract tests for the release workflow's wheel upload.

The upload step used to be `find dist/wheels-* ... | xargs -0 -r gh release
upload`. A pipeline reports the exit status of its last command, so the step
passed while uploading nothing, and two releases published without their wheel.
These tests hold the two properties that prevented it: the artefact is named on
download, and the search and its empty case run in Python where a failure can
fail the step.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
UPLOAD_SCRIPT = Path("scripts/upload_release_wheels.py")


def _release_steps() -> list[dict[str, typ.Any]]:
    """Return the steps of the workflow's ``release`` job."""
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["release"]["steps"]


def test_the_wheel_artefact_is_named_on_download() -> None:
    """The download names the artefact instead of trusting a layout.

    Without the name, the action's directory layout decides where the wheel
    lands, and a search written for one layout silently matches nothing under
    another.
    """
    downloads = [
        step
        for step in _release_steps()
        if str(step.get("uses", "")).startswith("actions/download-artifact")
    ]

    assert downloads, "the release job must download the built wheel"
    assert all(step.get("with", {}).get("name") for step in downloads), (
        "every artefact download must name the artefact it expects"
    )


def test_the_upload_runs_the_python_step() -> None:
    """The upload runs the script, which fails when no wheel is found."""
    commands = [str(step.get("run", "")) for step in _release_steps()]

    assert any(str(UPLOAD_SCRIPT) in command for command in commands), (
        f"the release job must upload wheels through {UPLOAD_SCRIPT}"
    )


@pytest.mark.parametrize("forbidden", ["xargs", "find dist"])
def test_the_upload_is_not_a_shell_pipeline(forbidden: str) -> None:
    """No release step may search for wheels through a shell pipeline.

    The exit status of a pipeline is its last command's, so ``set -eu`` cannot
    see the search fail. This assertion is what makes the regression loud.
    """
    commands = [str(step.get("run", "")) for step in _release_steps()]

    offenders = [command for command in commands if forbidden in command]
    assert not offenders, (
        f"release steps must not use {forbidden!r} to find wheels: {offenders}"
    )


def test_the_upload_step_receives_the_tag_and_a_token() -> None:
    """The script needs the tag it uploads to and credentials to do it."""
    upload_steps = [
        step
        for step in _release_steps()
        if str(UPLOAD_SCRIPT) in str(step.get("run", ""))
    ]

    assert len(upload_steps) == 1, f"expected exactly one upload step: {upload_steps}"
    environment = upload_steps[0].get("env", {})
    assert "GITHUB_REF_NAME" in environment, (
        "the script reads the tag from the ref name"
    )
    assert "GITHUB_TOKEN" in environment, "gh release upload needs a token"
