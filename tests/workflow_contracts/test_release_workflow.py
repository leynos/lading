"""Contracts for PyPI-first, idempotent release publication."""

import typing as typ

import yaml

from tests.workflow_contracts.step_harness import REPOSITORY_ROOT

RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/release.yml"
COLLECT_ACTION = (
    REPOSITORY_ROOT / ".github/actions/collect-release-artefacts/action.yml"
)
UPLOAD_SCRIPT = "scripts/upload_release_wheels.py"
ARTEFACT_NAME = "wheels-pure"
COLLECT_ACTION_REFERENCE = "./.github/actions/collect-release-artefacts"


def _workflow() -> dict[str, typ.Any]:
    """Load the release workflow, including PyYAML's YAML 1.1 ``on`` key."""
    document = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        message = "release.yml must decode to a mapping"
        raise TypeError(message)
    return document


def _job(name: str) -> dict[str, typ.Any]:
    """Return one named release job."""
    return _workflow()["jobs"][name]


def _steps(job: str) -> list[dict[str, typ.Any]]:
    """Return a job's steps with mappings preserved for readable assertions."""
    return _job(job)["steps"]


def _named_step(job: str, name: str) -> dict[str, typ.Any]:
    """Return the named step from ``job``."""
    return next(step for step in _steps(job) if step.get("name") == name)


def test_release_trigger_and_concurrency_are_tag_scoped() -> None:
    """A tag rerun is serialized and cannot cancel the active publication."""
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert triggers["push"]["tags"] == ["v*.*.*"], "only version tags should publish"
    assert workflow["permissions"] == {"contents": "read"}, (
        "the workflow should grant no job-wide write access"
    )
    assert workflow["concurrency"] == {
        "group": "release-${{ github.ref_name }}",
        "cancel-in-progress": False,
    }, "tag publication should be serialised without cancelling a run"


def test_jobs_publish_to_pypi_before_the_github_release() -> None:
    """The second target waits for successful PyPI publication."""
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert jobs["publish-pypi"]["needs"] == ["build-dist"], (
        "PyPI publication must wait for the distribution build"
    )
    assert jobs["release"]["needs"] == ["publish-pypi"], (
        "GitHub publication must wait for PyPI publication"
    )
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi", (
        "the OIDC publisher must use the configured PyPI environment"
    )
    assert jobs["publish-pypi"]["environment"]["url"] == (
        "https://pypi.org/project/lading/"
    ), "the workflow environment URL should identify the PyPI project"
    assert jobs["publish-pypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }, "only PyPI publication should receive an OIDC token"
    assert jobs["release"]["permissions"] == {"contents": "write"}, (
        "the GitHub publisher needs only release-content access"
    )
    assert "id-token" not in jobs["release"]["permissions"], (
        "the GitHub release job should not receive PyPI credentials"
    )


def test_collection_is_shared_by_both_publication_targets() -> None:
    """Each job stages the same named wheel-and-sdist build artefact."""
    for job in ("publish-pypi", "release"):
        uses = [step.get("uses") for step in _steps(job)]
        assert uses.count(COLLECT_ACTION_REFERENCE) == 1, (
            job,
            uses,
            "each publication job must collect the same build artefact",
        )

    action = yaml.safe_load(COLLECT_ACTION.read_text(encoding="utf-8"))
    download = next(
        step
        for step in action["runs"]["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
    )
    assert download["with"] == {"name": ARTEFACT_NAME, "path": "dist"}, (
        "collection should download the wheel-and-sdist artifact into dist"
    )


def test_pypi_order_uses_oidc_and_uv_simple_index_check() -> None:
    """Collection, interpreter setup, staging and publication run in order."""
    steps = _steps("publish-pypi")
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

    publisher = _named_step("publish-pypi", "Publish distributions to PyPI")
    assert "uv publish --trusted-publishing always" in publisher["run"], (
        "PyPI upload should use Trusted Publishing"
    )
    assert '--check-url "${PYPI_INDEX_URL}/simple/"' in publisher["run"], (
        "uv should verify the live index before upload"
    )
    assert publisher["env"]["PYPI_INDEX_URL"] == "https://pypi.org", (
        "the publisher should use the public PyPI index"
    )
    workflow_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "PYPI_TOKEN" not in workflow_text, "publication must not use a stored token"
    assert _job("publish-pypi")["permissions"]["id-token"] == "write", (
        "the publishing job must request an OIDC token"
    )


def test_github_release_is_reused_uploaded_and_published_in_order() -> None:
    """Only missing distributions attach before the release draft is cleared."""
    steps = _steps("release")
    names = [step.get("name") for step in steps]
    create = names.index("Create draft release if absent")
    upload = names.index("Upload distributions to GitHub release")
    publish = names.index("Publish GitHub release")
    assert names.index("Collect release artefacts") < create < upload < publish, (
        "the draft must be created before assets upload and final publication"
    )

    upload_step = steps[upload]
    assert UPLOAD_SCRIPT in upload_step["run"], "the release job must use the uploader"
    assert "--locked" in upload_step["run"], (
        "the credential-bearing step must be locked"
    )
    assert "--clobber" not in upload_step["run"], "reruns must not replace assets"
    assert upload_step["env"]["TAG"] == "${{ github.ref_name }}", (
        "the release tag should be supplied through step environment"
    )
    token_reference = upload_step["env"]["GH_TOKEN"]
    assert token_reference.startswith("${{ secrets."), (
        "GitHub credentials should be referenced as a secret"
    )
    assert token_reference.endswith("GITHUB_TOKEN }}"), (
        "the release job should use the GitHub-provided token"
    )
    assert "--draft --generate-notes" in steps[create]["run"], (
        "a newly created release should begin as a generated draft"
    )
    assert "--draft=false" in steps[publish]["run"], (
        "the final release step should publish the draft"
    )


def test_run_blocks_have_no_inline_expressions_or_clobbering_uploads() -> None:
    """All interpolation stays in env fields and release assets are idempotent."""
    for job_name, job in _workflow()["jobs"].items():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{" not in run, (
                job_name,
                step.get("name"),
                run,
                "GitHub expressions should be passed through environment fields",
            )
            assert "--clobber" not in run, (
                job_name,
                step.get("name"),
                run,
                "workflow uploads should preserve existing release assets",
            )


def test_shared_build_action_produces_the_named_release_artefact() -> None:
    """The release build checks out the repo before using the shared action."""
    steps = _steps("build-dist")
    assert len(steps) == 2, (
        "the release build should check out code and call the action"
    )
    assert steps[0]["uses"].startswith("actions/checkout@"), (
        "the release job must check out before loading a local action"
    )
    assert steps[1]["uses"] == "./.github/actions/pure-python-wheel", (
        "release builds should use the shared distribution action"
    )
    assert steps[1]["with"]["artifact-name"] == ARTEFACT_NAME, (
        "release builds should upload the artifact expected by collectors"
    )
    assert steps[1]["with"]["python-version"] == "3.14", (
        "the release build should match the project's Python baseline"
    )
