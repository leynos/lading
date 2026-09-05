"""Contract tests for CI's shared mdtablefix installer.

``make check-fmt`` validates tracked Markdown against canonical ``mdtablefix``
output, so CI must provide the formatter at the version the checker and
``make fmt`` agree on. These tests parse ``ci.yml`` with PyYAML and pin the
contract the installer must uphold: a prebuilt-only shared action, a version
passed through the workflow environment, and a cache keyed on that version.

Dependabot owns the upgrade of GitHub Actions and reusable workflows (see the
developers' guide), so the shared-action revision is asserted against a
constant in this module rather than a hard-coded copy inside each assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

# The shared action revision every leynos/shared-actions reference in this
# repository must agree on. Kept in one place so Dependabot's lockstep bump is
# a single-line edit.
SHARED_ACTION_REVISION = "c5a54701c8603a0fa756a6b34c49bc2af75a6c11"

_USES_RE = re.compile(r"^leynos/shared-actions/.+@(?P<sha>[0-9a-f]{40})$")


def _load() -> dict[str, object]:
    """Parse the CI workflow file."""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    """Return the named job, failing the test when it is missing."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "ci.yml must declare a jobs mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"ci.yml must declare the {name} job"
    return job


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    """Return the named step from a job, failing the test when absent."""
    steps = job.get("steps")
    assert isinstance(steps, list), "the CI job must declare steps"
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    message = f"the CI job must declare a {name!r} step"
    pytest.fail(message)
    raise AssertionError(message)


def _shared_action_uses() -> list[tuple[str, str]]:
    """Return every shared-actions reference across the workflow files."""
    references: list[tuple[str, str]] = []
    for workflow in sorted(WORKFLOW_PATH.parent.glob("*.yml")):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job in (document.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            uses = job.get("uses")
            if isinstance(uses, str) and uses.startswith("leynos/shared-actions/"):
                references.append((workflow.name, uses))
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                step_uses = step.get("uses")
                if isinstance(step_uses, str) and step_uses.startswith(
                    "leynos/shared-actions/"
                ):
                    references.append((workflow.name, step_uses))
    return references


def test_main_rust_toolchain_is_unchanged() -> None:
    """The formatter must not move the project's primary Rust toolchain."""
    repository_root = WORKFLOW_PATH.parents[2]
    makefile = (repository_root / "Makefile").read_text(encoding="utf-8")
    assert "MDTABLEFIX_RUST_VERSION" not in makefile, (
        "the Makefile must not declare a formatter-only Rust toolchain"
    )
    toolchain_file = repository_root / "rust-toolchain.toml"
    assert not toolchain_file.exists(), (
        "lading has no Rust build; a formatter-driven toolchain file must not appear"
    )


def test_mdtablefix_version_is_pinned() -> None:
    """The workflow pins the formatter version the checker expects."""
    workflow = _load()
    environment = workflow.get("env")
    assert isinstance(environment, dict), "ci.yml must declare a workflow env block"
    assert environment.get("MDTABLEFIX_VERSION") == "0.5.1", (
        "ci.yml must pin MDTABLEFIX_VERSION to 0.5.1"
    )
    assert "MDTABLEFIX_RUST_VERSION" not in environment, (
        "the removed formatter source-build toolchain variable must not return"
    )


def test_mdtablefix_cache_is_keyed_on_the_formatter_version() -> None:
    """The cache restores the shared action's install path for this version."""
    job = _job(_load(), "lint-test")
    cache = _step(job, "Cache mdtablefix")
    configuration = cache.get("with")
    assert isinstance(configuration, dict), "the cache step must declare with"
    paths = configuration.get("path")
    assert isinstance(paths, str), "the cache step must declare with.path as a string"
    assert "~/.local/bin/mdtablefix" in paths, (
        "the cache must cover the shared action's ~/.local/bin install path"
    )
    key = configuration.get("key")
    assert isinstance(key, str), "the cache step must declare with.key"
    assert "${{ env.MDTABLEFIX_VERSION }}" in key, (
        "the cache key must include the formatter version so a bump "
        "invalidates the cached executable"
    )
    assert "MDTABLEFIX_RUST_VERSION" not in key, (
        "the cache key must not include the removed formatter Rust toolchain"
    )
    assert "runs-on" in job or "runner.os" in key, (
        "the cache key must keep the repository's existing OS dimension"
    )


def test_install_mdtablefix_uses_the_shared_prebuilt_action() -> None:
    """CI delegates pinned prebuilt formatter installation to shared-actions."""
    job = _job(_load(), "lint-test")
    step = _step(job, "Install mdtablefix")

    expected = (
        "leynos/shared-actions/.github/actions/install-mdtablefix@"
        f"{SHARED_ACTION_REVISION}"
    )
    assert step.get("uses") == expected, (
        "the Install mdtablefix step must use the shared prebuilt installer at "
        "the requested shared-actions revision"
    )
    inputs = step.get("with")
    assert isinstance(inputs, dict), "the Install mdtablefix step must pass inputs"
    assert inputs.get("version") == "${{ env.MDTABLEFIX_VERSION }}", (
        "the Install mdtablefix step must pass the workflow's pinned version"
    )
    assert "run" not in step, (
        "the Install mdtablefix step must not retain a local source-build fallback"
    )


def test_all_shared_action_references_share_one_revision() -> None:
    """Every shared-actions reference is pinned to the requested revision."""
    references = _shared_action_uses()
    assert references, "the repository must reference leynos/shared-actions"
    for workflow_name, uses in references:
        assert uses.endswith(f"@{SHARED_ACTION_REVISION}"), (
            f"{workflow_name} references {uses}, which is not the requested "
            "shared-actions revision"
        )


def test_shared_action_references_are_full_commit_shas() -> None:
    """Shared-actions references stay pinned to full 40-hex commit SHAs."""
    for _workflow_name, uses in _shared_action_uses():
        ref = uses.split("@", 1)[1]
        assert _USES_RE.match(uses), f"expected a 40-hex commit SHA, got {ref!r}"
