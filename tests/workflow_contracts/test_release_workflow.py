"""Contracts for PyPI-first, idempotent release publication."""

import ast
import os
import re
import shlex
import subprocess
import typing as typ
from pathlib import Path, PurePosixPath

import pytest
import yaml

from tests.workflow_contracts.step_harness import (
    REPOSITORY_ROOT,
    WorkflowStep,
    _find_named_step,
    _steps,
)

RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/release.yml"
COLLECT_ACTION = (
    REPOSITORY_ROOT / ".github/actions/collect-release-artefacts/action.yml"
)
PURE_WHEEL_ACTION_YML = REPOSITORY_ROOT / ".github/actions/pure-python-wheel/action.yml"
PURE_WHEEL_UPLOAD_STEP = "Upload sdist and wheel artefact"
COLLECT_ACTION_REFERENCE = "./.github/actions/collect-release-artefacts"
COLLECT_VALIDATION_STEP = "Collect and validate release distributions"
UPLOAD_SCRIPT = "scripts/upload_release_wheels.py"
PURE_ARTIFACT_NAME = "wheels-pure"
CREATE_RELEASE_STEP = "Create draft release if absent"
COLLECT_RELEASE_STEP = "Collect release artefacts"
UPLOAD_STEP = "Upload distributions to GitHub release"
PUBLISH_STEP = "Publish GitHub release"
UPLOAD_COMMAND = ("uv", "run", "--locked", "--script", UPLOAD_SCRIPT)
PUBLISH_COMMAND = ("gh", "release", "edit")
GH_ENV_REFERENCE = "${{ secrets.GITHUB_TOKEN }}"
SHELL_CONTROL_OPERATOR_CHARACTERS = frozenset("&;|")
DEFAULT_UPLOAD_DIRECTORY = "dist"  # See scripts/upload_release_wheels.py.
StepControls = typ.TypedDict(
    "StepControls", {"continue-on-error": bool, "if": str}, total=False
)


def _workflow() -> dict[str, typ.Any]:
    """Load the release workflow, including PyYAML's YAML 1.1 ``on`` key."""
    document = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict), "release.yml must decode to a mapping"
    return document


def _load_job(job_name: str) -> dict[str, typ.Any]:
    """Return a named workflow job after validating its mapping boundaries."""
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict), f"{RELEASE_WORKFLOW} must define a jobs mapping"
    job = jobs.get(job_name)
    assert isinstance(job, dict), f"job {job_name!r} must decode to a mapping"
    return job


def _release_job() -> dict[str, typ.Any]:
    """Return the workflow's ``release`` job."""
    return _load_job("release")


def _job_steps(job_name: str) -> list[WorkflowStep]:
    """Return normalized workflow steps from the shared step harness."""
    return _steps(RELEASE_WORKFLOW, job=job_name)


def _pure_wheel_producers() -> list[WorkflowStep]:
    """Return the release workflow's pure-wheel producer steps."""
    return [
        step
        for step in _job_steps("build-dist")
        if step.get("uses") == "./.github/actions/pure-python-wheel"
    ]


def _uploader_default_directory() -> str:
    """Return the literal default of the uploader's directory option."""
    tree = ast.parse((REPOSITORY_ROOT / UPLOAD_SCRIPT).read_text(encoding="utf-8"))
    main = next((n for n in tree.body if getattr(n, "name", "") == "main"), None)
    assert isinstance(main, ast.FunctionDef), f"{UPLOAD_SCRIPT} must define main"
    assert main.args.kwonlyargs, "main must define the directory option"
    assert main.args.kwonlyargs[-1].arg == "directory", "directory must stay last"
    default = main.args.kw_defaults[-1]
    assert isinstance(default, ast.Call), "directory default must be a Path call"
    assert default.args, "directory default must have an argument"
    literal_default = ast.literal_eval(default.args[0])
    assert isinstance(literal_default, str), "directory default must be a string"
    return literal_default


def _needs(job_name: str) -> list[str]:
    """Normalize a job's string or list dependency declaration."""
    needs = _load_job(job_name).get("needs", [])
    if isinstance(needs, str):
        return [needs]
    assert isinstance(needs, list), f"{job_name} needs must be a list or string"
    assert all(isinstance(item, str) for item in needs), "needs entries must be strings"
    return needs


def _step_tokens(step: WorkflowStep, *, posix: bool = True) -> list[str]:
    """Tokenize a shell step with shell quoting and comments respected."""
    return shlex.split(step["run"], comments=True, posix=posix)


def _artifact_download_steps() -> list[WorkflowStep]:
    """Return downloads inside the shared composite collector."""
    return [
        step
        for step in _steps(COLLECT_ACTION)
        if step.get("uses", "").startswith("actions/download-artifact")
    ]


def _upload_directory(tokens: list[str]) -> str:
    """Return the uploader directory option or its command-line default."""
    for index, argument in enumerate(tokens):
        if argument.startswith("--directory="):
            return argument.partition("=")[2]
        if argument == "--directory":
            assert index + 1 < len(tokens), "--directory must have a value"
            return tokens[index + 1]
    return DEFAULT_UPLOAD_DIRECTORY


def _has_supported_directory_arguments(arguments: list[str]) -> bool:
    """Return whether optional directory arguments use a supported form."""
    return (
        len(arguments) == 0
        or (len(arguments) == 2 and arguments[0] == "--directory")
        or (len(arguments) == 1 and arguments[0].startswith("--directory="))
    )


def _is_upload_command(tokens: list[str]) -> bool:
    """Return whether tokens match the locked uploader's supported CLI shape."""
    arguments = tokens[len(UPLOAD_COMMAND) :]
    return (
        tokens[: len(UPLOAD_COMMAND)] == list(UPLOAD_COMMAND)
        and arguments[:2] == ["--tag", '"$TAG"']
        and _has_supported_directory_arguments(arguments[2:])
        and not any(set(token) & SHELL_CONTROL_OPERATOR_CHARACTERS for token in tokens)
    )


def _upload_tokens(step: WorkflowStep) -> list[str]:
    """Return a standalone invocation of the release wheel uploader."""
    tokens = _step_tokens(step, posix=False)
    assert _is_upload_command(tokens), f"unsupported upload command: {tokens}"
    return tokens


def _collection_publish_directory() -> str:
    """Derive the publish directory from the collector's checked-in script."""
    step = _find_named_step(COLLECT_ACTION, COLLECT_VALIDATION_STEP)
    distribution_directory = step.get("env", {}).get("DIST_DIRECTORY")
    assert isinstance(distribution_directory, str), "collector must name DIST_DIRECTORY"
    matches = re.findall(
        r'(?m)^\s*publish_directory="\$\{DIST_DIRECTORY\}/([^"$]+)"\s*$', step["run"]
    )
    assert len(matches) == 1, "collector must set one publish-directory assignment"
    relative_directory = PurePosixPath(matches[0])
    assert not relative_directory.is_absolute(), "collector directory must be relative"
    assert ".." not in relative_directory.parts, "collector path must stay relative"
    return (PurePosixPath(distribution_directory) / relative_directory).as_posix()


def test_release_trigger_jobs_and_permissions_are_tag_scoped() -> None:
    """A tag rerun is serialized and uses the intended release dependencies."""
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"push": {"tags": ["v*.*.*"]}}, "only version-tag pushes"
    assert workflow["permissions"] == {"contents": "read"}, (
        "the workflow should grant no job-wide write access"
    )
    assert workflow["concurrency"] == {
        "group": "release-${{ github.ref_name }}",
        "cancel-in-progress": False,
    }, "tag publication should be serialized without cancelling a run"

    assert "build-dist" in _needs("publish-pypi"), "PyPI must depend on the build"
    assert "publish-pypi" in _needs("release"), "release must depend on PyPI"
    pypi = workflow["jobs"]["publish-pypi"]
    release = workflow["jobs"]["release"]
    assert not pypi.get("continue-on-error"), "PyPI job must fail closed"
    assert pypi["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/project/lading/",
    }, "the OIDC publisher must use the configured PyPI environment"
    assert pypi["permissions"] == {"contents": "read", "id-token": "write"}, (
        "only PyPI publication should receive an OIDC token"
    )
    assert release["permissions"] == {"contents": "write"}, (
        "the GitHub publisher should receive only release-content access"
    )
    assert release.get("if") is None, "release job must preserve dependency gating"


def test_build_artifact_and_uploader_paths_agree() -> None:
    """Bind the producer, download, collector and uploader paths together."""
    assert _uploader_default_directory() == DEFAULT_UPLOAD_DIRECTORY, (
        "CLI default mismatch"
    )
    producers = _pure_wheel_producers()
    downloads = _artifact_download_steps()
    assert len(producers) == 1, f"expected one pure-wheel producer, got {producers}"
    assert len(downloads) == 1, f"expected one artifact download, got {downloads}"
    producer = producers[0]
    artifact_name = producer.get("with_", {}).get("artifact-name")
    wheel_upload = _find_named_step(PURE_WHEEL_ACTION_YML, PURE_WHEEL_UPLOAD_STEP)
    upload_uses = wheel_upload.get("uses", "")
    assert upload_uses.startswith("actions/upload-artifact@"), "must upload an artifact"
    upload_config = wheel_upload.get("with_", {})
    upload_paths = upload_config.get("path", "").splitlines()
    assert "${{ inputs.out-dir }}/*.whl" in upload_paths, "producer must upload wheels"
    assert "${{ inputs.out-dir }}/*.tar.gz" in upload_paths, "sdist upload missing"
    assert upload_config.get("if-no-files-found") == "error", "missing files must fail"
    upload_name = upload_config.get("name")
    assert upload_name == "${{ inputs.artifact-name }}", "name must use action input"
    download = downloads[0].get("with_", {})
    assert artifact_name == PURE_ARTIFACT_NAME, (
        f"producer must name its artifact {PURE_ARTIFACT_NAME!r}, got {artifact_name!r}"
    )
    assert download.get("name") == artifact_name, (
        f"collector must download producer artifact {artifact_name!r}, got {download}"
    )
    assert download.get("path") == "dist", "download path must be dist"
    collector_directory = _find_named_step(COLLECT_ACTION, COLLECT_VALIDATION_STEP)[
        "env"
    ]["DIST_DIRECTORY"]
    assert collector_directory == download["path"], "collector path must match download"
    for job_name in ("publish-pypi", "release"):
        uses = [step.get("uses") for step in _job_steps(job_name)]
        assert uses.count(COLLECT_ACTION_REFERENCE) == 1, (
            f"{job_name} must collect the shared artefact exactly once; "
            f"found steps {uses}"
        )
    upload = _find_named_step(RELEASE_WORKFLOW, UPLOAD_STEP, job="release")
    upload_directory = _upload_directory(_upload_tokens(upload))
    assert upload_directory == _collection_publish_directory(), "uploader path mismatch"


def test_pypi_order_uses_oidc_and_uv_simple_index_check() -> None:
    """Collection, interpreter setup, staging and publication run in order."""
    steps = _job_steps("publish-pypi")
    names = [step.get("name") for step in steps]
    assert (
        names.index("Collect release artefacts")
        < names.index("Set up Python")
        < names.index("Install uv")
        < names.index("Skip distributions already published to PyPI")
        < names.index("Publish distributions to PyPI")
    ), "collect, install, stage and publish distributions in order"
    publisher = _find_named_step(
        RELEASE_WORKFLOW, "Publish distributions to PyPI", job="publish-pypi"
    )
    pypi_controls = typ.cast("StepControls", publisher)
    assert pypi_controls.get("if") is None, "PyPI upload must remain dependency gated"
    assert "uv publish --trusted-publishing always" in publisher["run"], (
        "PyPI upload should use Trusted Publishing"
    )
    assert '--check-url "${PYPI_INDEX_URL}/simple/"' in publisher["run"], (
        "uv should verify the live index before upload"
    )
    assert publisher["env"]["PYPI_INDEX_URL"] == "https://pypi.org", (
        "the publisher should use the public PyPI index"
    )
    assert "PYPI_TOKEN" not in RELEASE_WORKFLOW.read_text(encoding="utf-8"), (
        "publication must not use a stored token"
    )
    assert _load_job("publish-pypi")["permissions"]["id-token"] == "write", (
        "the publishing job must request an OIDC token"
    )


def test_github_release_is_uploaded_and_published_in_order() -> None:
    """Validate artefacts before draft creation, upload before publication."""
    steps = _job_steps("release")
    run_steps = [step for step in steps if step.get("run")]
    assert len(run_steps) == 3, "release must have exactly three shell steps"
    assert {step.get("name") for step in run_steps} == {
        CREATE_RELEASE_STEP,
        UPLOAD_STEP,
        PUBLISH_STEP,
    }, f"unexpected release shell steps: {run_steps}"
    assert all(
        not {"find", "xargs"}.intersection(_step_tokens(step)) for step in run_steps
    ), "release steps must not search for artefacts"
    collector = _find_named_step(RELEASE_WORKFLOW, COLLECT_RELEASE_STEP, job="release")
    create = _find_named_step(RELEASE_WORKFLOW, CREATE_RELEASE_STEP, job="release")
    upload = _find_named_step(RELEASE_WORKFLOW, UPLOAD_STEP, job="release")
    publish = _find_named_step(RELEASE_WORKFLOW, PUBLISH_STEP, job="release")
    assert (
        steps.index(collector)
        < steps.index(create)
        < steps.index(upload)
        < steps.index(publish)
    ), "collect artefacts before drafting, then upload before publication"
    publication_tokens = [*PUBLISH_COMMAND, '"$TAG"', "--draft=false"]
    assert all(
        "--draft=false" not in _step_tokens(step)
        for step in steps[: steps.index(upload)]
        if step.get("run")
    ), "pre-upload steps must not publish the release"
    upload_tokens = _upload_tokens(upload)
    assert "--clobber" not in upload_tokens, "reruns must preserve existing assets"
    assert upload["env"]["TAG"] == "${{ github.ref_name }}", (
        "the uploader must receive the tag from the workflow ref"
    )
    publish_tokens = _step_tokens(publish, posix=False)
    assert publish_tokens == publication_tokens, (
        f"publish must run the standalone gh release edit command, got {publish_tokens}"
    )
    assert all(
        step["env"]["GH_REPO"] == "${{ github.repository }}"
        and step["env"]["GH_TOKEN"] == GH_ENV_REFERENCE
        and step["env"]["TAG"] == "${{ github.ref_name }}"
        for step in (create, upload, publish)
    ), "GitHub CLI operations should use the tag, workflow token and repository context"


@pytest.mark.parametrize("state", ["absent", "existing"])
def test_draft_release_creation_is_idempotent(tmp_path: Path, state: str) -> None:
    """Exercise draft creation for absent and existing releases."""
    create = _find_named_step(RELEASE_WORKFLOW, CREATE_RELEASE_STEP, job="release")
    gh_stub = (
        'gh() { printf "%s\\n" "$*" >> "$GH_STUB_LOG"; '
        'if [[ "$GH_STUB_STATE" == absent && "$2" == view ]]; then '
        'echo "release not found" >&2; return 1; fi; return 0; }\n'
    )
    call_log = tmp_path / state
    env = dict(
        os.environ,
        TAG="v-test",
        GH_STUB_STATE=state,
        GH_STUB_LOG=str(call_log),
        GH_HOST="github.invalid",
    )
    for token in ("GH_TOKEN", "GITHUB_TOKEN", "BASH_ENV"):
        env.pop(token, None)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - executes checked-in workflow shell with a recording stub
        ["/bin/bash", "-e", "-o", "pipefail", "-c", gh_stub + create["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert result.returncode == 0, f"{state} release probe failed: {result.stderr}"
    expected_calls = ["release view v-test"]
    if state == "absent":
        expected_calls.append("release create v-test --draft --generate-notes")
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls == expected_calls, f"{state} release invoked gh unexpectedly"


def test_release_publication_steps_fail_closed() -> None:
    """Critical release steps cannot continue or override failure handling."""
    critical_steps = _artifact_download_steps()
    for name in (COLLECT_RELEASE_STEP, CREATE_RELEASE_STEP, UPLOAD_STEP, PUBLISH_STEP):
        critical_steps.append(_find_named_step(RELEASE_WORKFLOW, name, job="release"))
    critical_steps.extend(_job_steps("publish-pypi"))
    critical_steps.append(_find_named_step(COLLECT_ACTION, COLLECT_VALIDATION_STEP))
    assert not any(
        typ.cast("StepControls", step).get("continue-on-error")
        for step in critical_steps
    ), "critical release steps must fail closed"
    assert not _release_job().get("continue-on-error"), "release job fails closed"
    upload = _find_named_step(RELEASE_WORKFLOW, UPLOAD_STEP, job="release")
    assert typ.cast("StepControls", upload).get("if") is None, "unexpected upload if"
    publish = _find_named_step(RELEASE_WORKFLOW, PUBLISH_STEP, job="release")
    assert typ.cast("StepControls", publish).get("if") is None, "unexpected publish if"


def test_run_blocks_have_no_inline_expressions_or_clobbering_uploads() -> None:
    """Expressions stay in env fields and workflow uploads remain idempotent."""
    for job_name in _workflow()["jobs"]:
        for step in _job_steps(job_name):
            run = step.get("run", "")
            context = f"{job_name}/{step.get('name')}"
            assert "${{" not in run, f"{context}: expressions belong in env, not run"
            assert "--clobber" not in run, f"{context}: uploads must not clobber assets"


def test_release_jobs_do_not_persist_checkout_credentials() -> None:
    """Release jobs should not pass checkout credentials to later commands."""
    for job_name in ("build-dist", "publish-pypi", "release"):
        checkouts = [
            step
            for step in _job_steps(job_name)
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        assert len(checkouts) == 1, f"{job_name} must have one checkout"
        assert checkouts[0].get("with_", {}).get("persist-credentials") is False, (
            f"{job_name} checkout must not persist credentials"
        )
