"""Contracts for PyPI-first, idempotent release publication."""

from __future__ import annotations

import re
import shlex
import typing as typ
from pathlib import Path, PurePosixPath

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
PURE_WHEEL_ACTION = "./.github/actions/pure-python-wheel"
COLLECT_ACTION_REFERENCE = "./.github/actions/collect-release-artefacts"
UPLOAD_SCRIPT = "scripts/upload_release_wheels.py"
PURE_ARTIFACT_NAME = "wheels-pure"
CREATE_RELEASE_STEP = "Create draft release if absent"
COLLECT_RELEASE_STEP = "Collect release artefacts"
UPLOAD_STEP = "Upload distributions to GitHub release"
PUBLISH_STEP = "Publish GitHub release"
UPLOAD_COMMAND = ("uv", "run", "--locked", "--script", UPLOAD_SCRIPT)
PUBLISH_COMMAND = ("gh", "release", "edit")
SHELL_CONTROL_OPERATOR_CHARACTERS = frozenset("&;|")
# The CLI's default is declared by scripts/upload_release_wheels.py.
DEFAULT_UPLOAD_DIRECTORY = "dist"


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


def _needs(job_name: str) -> list[str]:
    """Normalize a job's string or list dependency declaration."""
    needs = _load_job(job_name).get("needs", [])
    if isinstance(needs, str):
        return [needs]
    assert isinstance(needs, list) and all(isinstance(item, str) for item in needs), (
        f"{job_name} needs must be a string or list of strings, got {needs!r}"
    )
    return needs


def _step_tokens(step: WorkflowStep) -> list[str]:
    """Tokenize a shell step with shell quoting and comments respected."""
    return shlex.split(step["run"], comments=True)


def _shell_control_operators(step: WorkflowStep) -> set[str]:
    """Return parsed shell control operators present in a workflow step."""
    lexer = shlex.shlex(step["run"], posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return {
        token
        for token in lexer
        if token and set(token) <= SHELL_CONTROL_OPERATOR_CHARACTERS
    }


def _artifact_download_steps() -> list[WorkflowStep]:
    """Return downloads inside the shared composite collector."""
    return [
        step
        for step in _steps(COLLECT_ACTION)
        if step.get("uses", "").startswith("actions/download-artifact")
    ]


def _wheel_producer_steps() -> list[WorkflowStep]:
    """Return pure-wheel artifact producers in the release build job."""
    return [
        step
        for step in _job_steps("build-dist")
        if step.get("uses") == PURE_WHEEL_ACTION
    ]


def _upload_directory(tokens: list[str]) -> str:
    """Return the uploader directory option or its command-line default."""
    for index, argument in enumerate(tokens):
        if argument.startswith("--directory="):
            return argument.partition("=")[2]
        if argument != "--directory":
            continue
        if index + 1 == len(tokens):
            raise ValueError("--directory must have a value")
        return tokens[index + 1]
    return DEFAULT_UPLOAD_DIRECTORY


def _is_upload_command(tokens: list[str]) -> bool:
    """Return whether tokens match the locked uploader's supported CLI shape."""
    arguments = tokens[len(UPLOAD_COMMAND) :]
    directory_arguments = arguments[2:]
    return (
        tokens[: len(UPLOAD_COMMAND)] == list(UPLOAD_COMMAND)
        and arguments[:2] == ["--tag", "$TAG"]
        and (
            not directory_arguments
            or (
                len(directory_arguments) == 2
                and directory_arguments[0] == "--directory"
            )
            or (
                len(directory_arguments) == 1
                and directory_arguments[0].startswith("--directory=")
            )
        )
        and not any(set(token) & SHELL_CONTROL_OPERATOR_CHARACTERS for token in tokens)
    )


def _upload_tokens(step: WorkflowStep) -> list[str]:
    """Return a standalone invocation of the release wheel uploader."""
    tokens = _step_tokens(step)
    assert _is_upload_command(tokens), (
        f"upload must run the checked uploader, got {tokens}"
    )
    assert not _shell_control_operators(step), (
        "the upload command must fail when the uploader fails"
    )
    return tokens


def _collection_publish_directory() -> str:
    """Derive the publish directory from the collector's checked-in script."""
    step = _find_named_step(
        COLLECT_ACTION, "Collect and validate release distributions"
    )
    distribution_directory = step.get("env", {}).get("DIST_DIRECTORY")
    assert isinstance(distribution_directory, str), (
        f"collector must name DIST_DIRECTORY, got {distribution_directory!r}"
    )
    matches = re.findall(
        r'(?m)^\s*publish_directory="\$\{DIST_DIRECTORY\}/([^"$]+)"\s*$',
        step["run"],
    )
    assert len(matches) == 1, (
        f"expected one publish-directory assignment, got {matches}"
    )
    relative_directory = PurePosixPath(matches[0])
    assert (
        not relative_directory.is_absolute() and ".." not in relative_directory.parts
    ), f"collector publish directory must stay under DIST_DIRECTORY: {matches[0]!r}"
    return (PurePosixPath(distribution_directory) / relative_directory).as_posix()


def test_release_trigger_jobs_and_permissions_are_tag_scoped() -> None:
    """A tag rerun is serialized and uses the intended release dependencies."""
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert triggers["push"]["tags"] == ["v*.*.*"], "only version tags should publish"
    assert workflow["permissions"] == {"contents": "read"}, (
        "the workflow should grant no job-wide write access"
    )
    assert workflow["concurrency"] == {
        "group": "release-${{ github.ref_name }}",
        "cancel-in-progress": False,
    }, "tag publication should be serialized without cancelling a run"

    assert "build-dist" in _needs("publish-pypi"), (
        "PyPI publication must wait for the distribution build"
    )
    assert "publish-pypi" in _needs("release"), (
        "GitHub publication must wait for PyPI publication"
    )
    pypi = workflow["jobs"]["publish-pypi"]
    release = workflow["jobs"]["release"]
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


def test_build_artifact_and_uploader_paths_agree() -> None:
    """Bind the producer, download, collector and uploader paths together."""
    producers = _wheel_producer_steps()
    downloads = _artifact_download_steps()
    assert len(producers) == 1, f"expected one pure-wheel producer, got {producers}"
    assert len(downloads) == 1, f"expected one artifact download, got {downloads}"

    producer = producers[0]
    artifact_name = producer.get("with_", {}).get("artifact-name")
    download = downloads[0].get("with_", {})
    assert artifact_name == PURE_ARTIFACT_NAME, (
        f"producer must name its artifact {PURE_ARTIFACT_NAME!r}, got {artifact_name!r}"
    )
    assert download.get("name") == artifact_name, (
        f"collector must download producer artifact {artifact_name!r}, got {download}"
    )
    assert download.get("path") == "dist", (
        f"collector must download distributions into dist, got {download}"
    )
    collector_directory = _find_named_step(
        COLLECT_ACTION, "Collect and validate release distributions"
    )["env"]["DIST_DIRECTORY"]
    assert collector_directory == download["path"], (
        "collector validation must scan the artifact extraction directory"
    )
    assert producer.get("with_", {}).get("python-version") == "3.14", (
        "the release build should match the project's Python baseline"
    )

    for job_name in ("publish-pypi", "release"):
        uses = [step.get("uses") for step in _job_steps(job_name)]
        assert uses.count(COLLECT_ACTION_REFERENCE) == 1, (
            job_name,
            uses,
            "each publisher must collect the same build artifact",
        )
    upload = _find_named_step(RELEASE_WORKFLOW, UPLOAD_STEP, job="release")
    upload_directory = _upload_directory(_upload_tokens(upload))
    assert upload_directory == _collection_publish_directory(), (
        "the uploader must use the collector's validated publish directory"
    )


def test_pypi_order_uses_oidc_and_uv_simple_index_check() -> None:
    """Collection, interpreter setup, staging and publication run in order."""
    steps = _job_steps("publish-pypi")
    names = [step.get("name") for step in steps]
    assert names.index("Collect release artefacts") < names.index("Set up Python"), (
        "artifacts must be collected before Python setup"
    )
    assert names.index("Set up Python") < names.index("Install uv"), (
        "uv setup requires the selected Python interpreter"
    )
    assert (
        names.index("Install uv")
        < names.index("Skip distributions already published to PyPI")
        < names.index("Publish distributions to PyPI")
    ), "staging and publication must follow uv setup in order"

    publisher = _find_named_step(
        RELEASE_WORKFLOW, "Publish distributions to PyPI", job="publish-pypi"
    )
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
    """Validate artifacts before draft creation, upload before publication."""
    steps = _job_steps("release")
    for step in steps:
        if step.get("run"):
            assert not {"find", "xargs"}.intersection(_step_tokens(step)), (
                f"release step {step.get('name')!r} must not search for artifacts"
            )
    collector = _find_named_step(RELEASE_WORKFLOW, COLLECT_RELEASE_STEP, job="release")
    create = _find_named_step(RELEASE_WORKFLOW, CREATE_RELEASE_STEP, job="release")
    upload = _find_named_step(RELEASE_WORKFLOW, UPLOAD_STEP, job="release")
    publish = _find_named_step(RELEASE_WORKFLOW, PUBLISH_STEP, job="release")
    assert (
        steps.index(collector)
        < steps.index(create)
        < steps.index(upload)
        < steps.index(publish)
    ), "collect artifacts before drafting, then upload before publication"

    create_tokens = _step_tokens(create)
    create_index = create_tokens.index("create")
    assert create_tokens[create_index : create_index + 4] == [
        "create",
        "$TAG",
        "--draft",
        "--generate-notes",
    ], "a new release should begin as a generated draft"
    upload_tokens = _upload_tokens(upload)
    assert "--clobber" not in upload_tokens, "reruns must preserve existing assets"
    assert upload["env"]["TAG"] == "${{ github.ref_name }}", (
        "the uploader must receive the tag from the workflow ref"
    )

    publish_tokens = _step_tokens(publish)
    assert publish_tokens == [*PUBLISH_COMMAND, "$TAG", "--draft=false"], (
        f"publish must run the standalone gh release edit command, got {publish_tokens}"
    )
    assert not _shell_control_operators(publish), (
        "publication must fail when gh release edit fails"
    )
    for step in (create, upload, publish):
        assert step["env"]["GH_REPO"] == "${{ github.repository }}", (
            "GitHub CLI operations should use an explicit repository context"
        )
        assert step["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}", (
            "GitHub release operations should use the workflow token"
        )


def test_release_publication_steps_fail_closed() -> None:
    """Critical release steps cannot continue or override failure handling."""
    critical_workflow_steps = [
        _find_named_step(RELEASE_WORKFLOW, name, job="release")
        for name in (
            COLLECT_RELEASE_STEP,
            CREATE_RELEASE_STEP,
            UPLOAD_STEP,
            PUBLISH_STEP,
        )
    ]
    critical_action_steps = [
        *_artifact_download_steps(),
        _find_named_step(COLLECT_ACTION, "Collect and validate release distributions"),
    ]
    for step in (*critical_workflow_steps, *critical_action_steps):
        assert not step.get("continue-on-error"), (
            f"release step must not continue after failure: {step}"
        )
    assert not _release_job().get("continue-on-error"), (
        "the release job must not continue after a step failure"
    )

    condition = str(
        _find_named_step(RELEASE_WORKFLOW, PUBLISH_STEP, job="release").get("if", "")
    ).lower()
    assert not any(
        name in condition for name in ("always()", "failure()", "cancelled()")
    ), f"publish condition must not override failure handling: {condition!r}"


def test_run_blocks_have_no_inline_expressions_or_clobbering_uploads() -> None:
    """Expressions stay in env fields and workflow uploads remain idempotent."""
    for job_name in _workflow()["jobs"]:
        for step in _job_steps(job_name):
            run = step.get("run", "")
            assert "${{" not in run and "--clobber" not in run, (
                job_name,
                step.get("name"),
                run,
                "expressions belong in env and uploads preserve existing assets",
            )


def test_release_jobs_do_not_persist_checkout_credentials() -> None:
    """Release jobs should not pass checkout credentials to later commands."""
    for job_name in ("build-dist", "publish-pypi", "release"):
        checkout = next(
            step
            for step in _job_steps(job_name)
            if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout.get("with_", {}).get("persist-credentials") is False, (
            job_name,
            "release checkouts must not persist credentials in .git/config",
        )
