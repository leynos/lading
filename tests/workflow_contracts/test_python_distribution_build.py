"""Build contracts for the shared pure-Python distribution action."""

import re
from pathlib import Path

import pytest
import yaml

from tests.workflow_contracts.step_harness import (
    REPOSITORY_ROOT,
    _find_named_step,
    _find_step,
    _run_step,
)

ACTION = REPOSITORY_ROOT / ".github" / "actions" / "pure-python-wheel" / "action.yml"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.mark.parametrize(
    ("files", "status", "diagnostic"),
    [
        (("lading-1.0.tar.gz", "lading-1.0-py3-none-any.whl"), 0, None),
        (
            ("lading-1.0-py3-none-any.whl",),
            1,
            "Expected exactly one source distribution.",
        ),
        (
            ("lading-1.0.tar.gz", "lading-2.0.tar.gz", "lading-1.0.whl"),
            1,
            "Expected exactly one source distribution.",
        ),
        (("lading-1.0.tar.gz",), 1, "Expected at least one wheel."),
    ],
    ids=["valid", "missing-sdist", "two-sdists", "missing-wheel"],
)
def test_build_output_check_requires_exactly_one_sdist(
    tmp_path: Path,
    files: tuple[str, ...],
    status: int,
    diagnostic: str | None,
) -> None:
    """The action rejects invalid sdist and wheel counts with clear errors."""
    output = tmp_path / "dist"
    output.mkdir()
    for filename in files:
        (output / filename).touch()

    step = _find_step(ACTION, "Check build outputs")
    completed = _run_step(step, tmp_path, env={"OUT_DIR": str(output)})

    assert completed.returncode == status, completed.stderr
    if diagnostic:
        assert diagnostic in completed.stderr, (
            "the output check must explain the invalid distribution count"
        )


def test_build_action_builds_from_the_sdist_and_uploads_both_formats() -> None:
    """The shared action builds both files and includes both in its artefact."""
    build = _find_step(ACTION, "Build sdist and wheel")
    upload = _find_named_step(ACTION, "Upload sdist and wheel artefact")
    source = build["run"]
    artifact_paths = upload.get("with_", {}).get("path", "")

    assert "uv build --out-dir" in source, "the build must use plain uv build"
    assert "--wheel" not in source, "the wheel must be built from the sdist"
    assert "*.whl" in artifact_paths, "the uploaded artefact must contain wheels"
    assert "*.tar.gz" in artifact_paths, "the uploaded artefact must contain the sdist"
    assert upload.get("with_", {}).get("if-no-files-found") == "error", (
        "the action must fail when either distribution is absent"
    )
    assert all(
        not step.get("uses", "").startswith("actions/checkout")
        for step in yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    ), "the caller must own checkout before loading this local action"


def test_ci_builds_distributions_on_pull_requests_and_main_pushes() -> None:
    """The standalone build lane runs on both required CI event types."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    build_dist = workflow["jobs"]["build-dist"]

    assert "pull_request" in triggers, "pull requests must build an sdist"
    assert triggers["push"]["branches"] == ["main"], (
        "pushes to main must build an sdist"
    )
    assert build_dist["permissions"] == {"contents": "read"}, (
        "the CI build must have read-only repository access"
    )
    action_steps = build_dist["steps"]
    assert len(action_steps) == 2, (
        "the build job must check out and use the shared action"
    )
    checkout_reference = action_steps[0]["uses"].split("@", maxsplit=1)[-1]
    assert re.fullmatch(r"[0-9a-f]{40}", checkout_reference), (
        "the checkout action must use an immutable commit reference"
    )
    assert action_steps[0].get("with", {}).get("persist-credentials") is False, (
        "the CI build must not persist its checkout token for build steps"
    )
    assert action_steps[1]["uses"] == "./.github/actions/pure-python-wheel", (
        "the CI build must exercise the release distribution action"
    )
    assert action_steps[1]["with"]["artifact-name"] == "wheels-pure-ci", (
        "the CI build artefact must remain distinct from the release artefact"
    )
    assert action_steps[1]["with"]["python-version"] == "3.14", (
        "the CI build must use the repository Python baseline"
    )
