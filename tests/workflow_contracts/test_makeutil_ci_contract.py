"""Contract test for how CI jobs provision and verify the Makefile parser.

Every workflow that runs the full suite installs Makeutil through the shared
action, at the action's default version, and verifies the installed binary.
The workflows are parsed and asserted structurally.
"""

import re
import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_MAKEUTIL_ACTION: typ.Final = (
    "leynos/shared-actions/.github/actions/install-makeutil"
    "@ebe2f3105334283441f4c915eb979a0f4b9f2c04"
)


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object, naming the unexpected `subject` on failure."""
    assert isinstance(value, dict), f"Expected {subject} to be a JSON object."
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object list, naming the unexpected `subject` on failure."""
    assert isinstance(value, list), f"Expected {subject} to be a JSON array."
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return the named job from a repository workflow."""
    workflow = yaml.safe_load((REPOSITORY_ROOT / workflow_path).read_text())
    workflow_mapping = _mapping(workflow, subject=f"{workflow_path} workflow")
    jobs = _mapping(workflow_mapping.get("jobs"), subject=f"{workflow_path} jobs")
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} job {job_name!r}")


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named workflow step from the requested job."""
    job = _workflow_job(workflow_path, job_name)
    steps = _objects(
        job.get("steps"), subject=f"{workflow_path} job {job_name!r} steps"
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"Expected one {step_name!r} step in {workflow_path} job {job_name!r}, "
        f"found {len(matches)}."
    )
    return matches[0]


def _assert_makeutil_installation(step: dict[str, object], *, contract: str) -> None:
    """Assert that `step` runs the pinned prebuilt install action, defaults only.

    A `run` key would mean a from-source install had crept back, and a `with`
    key would move the version off the action's own default and digest table.
    """
    assert step.get("uses") == _INSTALL_MAKEUTIL_ACTION, (
        f"{contract} must use the pinned install-makeutil action."
    )
    assert "run" not in step, f"{contract} must not also run an install command."
    assert "with" not in step, f"{contract} must take the action's default version."


def _assert_makeutil_verification(
    install_step: dict[str, object],
    verify_step: dict[str, object],
    *,
    contract: str,
) -> None:
    """Assert the smoke step that proves the installed binary is usable.

    The install step needs an id so the verify step can read the version the
    action reports; the verify step must compare the binary's own version with
    it and require a complete parse of the repository Makefile.
    """
    assert install_step.get("id") == "makeutil", f"{contract} install step needs id"
    environment = _mapping(verify_step.get("env"), subject=f"{contract} environment")
    assert environment.get("INSTALLED_VERSION") == (
        "${{ steps.makeutil.outputs.version }}"
    ), f"{contract} must read the version the install action reports."
    script = verify_step.get("run")
    assert isinstance(script, str), f"{contract} must run a verification script."
    assert 'test "$(makeutil --version)" = "makeutil ${INSTALLED_VERSION}"' in script, (
        f"{contract} must compare the binary's version with the installed one."
    )
    assert "makeutil parse Makefile" in script, (
        f"{contract} must parse the repository Makefile."
    )
    assert '["parse"]["status"] == "complete"' in script, (
        f"{contract} must require a complete parse."
    )
    assert not re.search(r"\d+\.\d+\.\d+", script), (
        f"{contract} must compare versions, never name one."
    )


def _assert_verification_follows_install(workflow_path: str, job_name: str) -> None:
    """Assert the verify step directly follows the install step."""
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"),
        subject=f"{workflow_path} {job_name} steps",
    )
    names = [step.get("name") for step in steps]
    position = names.index("Install makeutil")
    assert names[position + 1] == "Verify makeutil", (
        f"{workflow_path} {job_name} must verify makeutil right after installing it."
    )


def test_full_suite_workflows_provision_pinned_makeutil() -> None:
    """Every workflow that runs the full suite must install Makeutil itself."""
    lint_step = _sole_workflow_step(
        ".github/workflows/ci.yml",
        "lint-test",
        "Run linters, including Skylos dead-code detection",
    )
    assert lint_step.get("run") == "make lint", (
        "CI lint step must invoke the shared make lint target."
    )
    for workflow_path, job_name in (
        (".github/workflows/ci.yml", "lint-test"),
        (".github/workflows/coverage-main.yml", "coverage-upload"),
    ):
        job = _workflow_job(workflow_path, job_name)
        environment = _mapping(
            job.get("env", {}), subject=f"{workflow_path} {job_name} environment"
        )
        assert "MAKEUTIL_REVISION" not in environment, (
            f"{workflow_path} {job_name} must not carry a from-source Makeutil pin."
        )
        assert "MAKEUTIL_TOOLCHAIN" not in environment, (
            f"{workflow_path} {job_name} must not carry a Makeutil nightly toolchain."
        )
        parser_step = _sole_workflow_step(workflow_path, job_name, "Install makeutil")
        _assert_makeutil_installation(
            parser_step,
            contract=f"{workflow_path} {job_name} Makeutil-install contract",
        )
        _assert_makeutil_verification(
            parser_step,
            _sole_workflow_step(workflow_path, job_name, "Verify makeutil"),
            contract=f"{workflow_path} {job_name} Makeutil-verify contract",
        )
        _assert_verification_follows_install(workflow_path, job_name)
