"""Contract tests for CI's shared mdtablefix installer.

``make check-fmt`` validates tracked Markdown against canonical ``mdtablefix``
output, so CI must provide the formatter at the version the checker and
``make fmt`` agree on. These tests parse ``ci.yml`` with PyYAML and verify the
stable installer contract: its shared-action path, formatter version, and full
commit SHA.

Dependabot owns the upgrade of GitHub Actions and reusable workflows (see the
developers' guide), so the installer reference is checked for its path and
full-SHA shape rather than against a hard-coded revision.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

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


def _is_shared_actions_uses_value(value: object) -> typ.TypeIs[str]:
    """Return whether a YAML uses value references shared-actions."""
    return isinstance(value, str) and value.startswith("leynos/shared-actions/")


def _job_shared_action_uses(job: object) -> list[str]:
    """Return shared-actions uses references from one YAML job."""
    if not isinstance(job, dict):
        return []

    references: list[str] = []
    job_uses = job.get("uses")
    if _is_shared_actions_uses_value(job_uses):
        references.append(job_uses)

    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_uses = step.get("uses")
        if _is_shared_actions_uses_value(step_uses):
            references.append(step_uses)
    return references


def _shared_action_uses() -> list[tuple[str, str]]:
    """Return every shared-actions reference across the workflow files."""
    references: list[tuple[str, str]] = []
    for workflow in sorted(WORKFLOW_PATH.parent.glob("*.yml")):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job in (document.get("jobs") or {}).values():
            references.extend(
                (workflow.name, uses) for uses in _job_shared_action_uses(job)
            )
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
    job = _job(_load(), "lint-test")
    step = _step(job, "Install mdtablefix")
    inputs = step.get("with")
    assert isinstance(inputs, dict), "the Install mdtablefix step must pass inputs"
    assert inputs.get("version") == "0.6.1", (
        "the Install mdtablefix step must pin the version with native --check support"
    )


def test_install_mdtablefix_uses_the_shared_prebuilt_action() -> None:
    """CI delegates pinned prebuilt formatter installation to shared-actions."""
    job = _job(_load(), "lint-test")
    step = _step(job, "Install mdtablefix")

    uses = step.get("uses")
    expected_path = "leynos/shared-actions/.github/actions/install-mdtablefix@"
    assert isinstance(uses, str), "the Install mdtablefix step must use an action"
    assert uses.startswith(expected_path), (
        "the Install mdtablefix step must use the shared prebuilt installer"
    )
    assert _USES_RE.fullmatch(uses), (
        "the Install mdtablefix action must be pinned to a full commit SHA"
    )
    inputs = step.get("with")
    assert isinstance(inputs, dict), "the Install mdtablefix step must pass inputs"
    assert inputs.get("version") == "0.6.1", (
        "the Install mdtablefix step must pass the formatter version"
    )
    assert "run" not in step, (
        "the Install mdtablefix step must not retain a local source-build fallback"
    )


def test_shared_action_references_are_full_commit_shas() -> None:
    """Shared-actions references stay pinned to full 40-hex commit SHAs."""
    for _workflow_name, uses in _shared_action_uses():
        ref = uses.split("@", 1)[1]
        assert _USES_RE.match(uses), f"expected a 40-hex commit SHA, got {ref!r}"


def test_shared_action_uses_handles_yaml_job_and_step_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Job and step references survive non-mapping YAML entries."""
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "a.yml").write_text(
        """\
jobs:
  reusable:
    uses: leynos/shared-actions/.github/workflows/reusable.yml@job
  lint:
    steps:
      - uses: leynos/shared-actions/.github/actions/lint@step
      - invalid-step
      - run: true
  invalid-job: invalid-job
  invalid-list: []
""",
        encoding="utf-8",
    )
    (workflows / "b.yml").write_text(
        """\
jobs:
  no-reference:
    steps:
      - uses: actions/checkout@v7
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(__name__ + ".WORKFLOW_PATH", workflows / "ci.yml")

    assert _shared_action_uses() == [
        ("a.yml", "leynos/shared-actions/.github/workflows/reusable.yml@job"),
        ("a.yml", "leynos/shared-actions/.github/actions/lint@step"),
    ], "the collector must retain valid job and step references in sorted workflows"
