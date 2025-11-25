"""Shared step implementations for CLI behaviour tests."""

from __future__ import annotations

import dataclasses as dc
import subprocess
import sys
import typing as typ

from pytest_bdd import parsers, scenarios, then
from tomlkit.items import InlineTable, Item, Table

from lading.testing import toml_utils

if typ.TYPE_CHECKING:
    from pathlib import Path

scenarios("../features/cli.feature")


def _run_cli(
    repo_root: Path,
    workspace_directory: Path,
    *command_args: str,
) -> dict[str, typ.Any]:
    command = [
        sys.executable,
        "-m",
        "lading.cli",
        "--workspace-root",
        str(workspace_directory),
        *command_args,
    ]
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "workspace": workspace_directory.resolve(),
    }


@then(parsers.parse("the CLI exits with code {expected:d}"))
def then_cli_exit_code(cli_run: dict[str, typ.Any], expected: int) -> None:
    """Assert that the CLI terminated with ``expected`` exit code."""
    assert cli_run["returncode"] == expected


@then(parsers.parse('the stderr contains "{expected}"'))
def then_stderr_contains(cli_run: dict[str, typ.Any], expected: str) -> None:
    """Assert that ``expected`` appears in the captured stderr output."""
    assert expected in cli_run["stderr"]


@then(parsers.parse('the workspace manifest version is "{version}"'))
def then_workspace_manifest_version(
    cli_run: dict[str, typ.Any],
    version: str,
) -> None:
    """Validate the workspace manifest was updated to ``version``."""
    manifest_path = cli_run["workspace"] / "Cargo.toml"
    document = toml_utils.load_manifest(manifest_path)
    workspace_package = document["workspace"]["package"]
    assert workspace_package["version"] == version


@then(parsers.parse('the crate "{crate_name}" manifest version is "{version}"'))
def then_crate_manifest_version(
    cli_run: dict[str, typ.Any],
    crate_name: str,
    version: str,
) -> None:
    """Validate the crate manifest was updated to ``version``."""
    manifest_path = cli_run["workspace"] / "crates" / crate_name / "Cargo.toml"
    document = toml_utils.load_manifest(manifest_path)
    assert document["package"]["version"] == version


def _extract_dependency_requirement(entry: object) -> str:
    """Return the version requirement string recorded in a dependency entry."""
    if isinstance(entry, Item):
        value = entry.value
        if isinstance(value, str):
            return value
    if isinstance(entry, str):
        return entry
    if isinstance(entry, InlineTable | Table):
        version_value = entry.get("version")
        return _extract_dependency_requirement(version_value)
    message = f"Dependency version entry is not a string: {entry!r}"
    raise AssertionError(message)


@dc.dataclass(frozen=True, slots=True)
class DependencyCheck:
    """Specification for checking a dependency requirement."""

    crate_name: str
    dependency_name: str
    section: str
    expected_requirement: str


def then_dependency_requirement(
    cli_run: dict[str, typ.Any],
    check: DependencyCheck,
) -> None:
    """Assert that an internal dependency requirement reflects the new version."""
    crate_name = check.crate_name
    dependency_name = check.dependency_name
    section = check.section
    expected = check.expected_requirement
    manifest_path = cli_run["workspace"] / "crates" / crate_name / "Cargo.toml"
    document = toml_utils.load_manifest(manifest_path)
    try:
        dependency_table = document[section]
    except KeyError as exc:  # pragma: no cover - defensive guard
        message = f"Section {section!r} missing from manifest {manifest_path}"
        raise AssertionError(message) from exc
    entry = dependency_table.get(dependency_name)
    if entry is None:
        message = (
            "Dependency "
            f"{dependency_name!r} missing from section {section!r} in {manifest_path}"
        )
        raise AssertionError(message)
    requirement = _extract_dependency_requirement(entry)
    assert requirement == expected


@then(parsers.parse('the dependency "{dependency_spec}" has requirement "{expected}"'))
def _then_dependency_requirement_step(
    cli_run: dict[str, typ.Any],
    dependency_spec: str,
    expected: str,
) -> None:
    """Assert that an internal dependency requirement reflects the new version.

    The dependency_spec should be in the format: "crate_name:dependency_name@section"
    Example: "beta:alpha@dependencies"
    """
    parts = dependency_spec.split(":", maxsplit=1)
    if len(parts) != 2:
        message = (
            "Dependency specification must contain exactly one ':' separator: "
            f"{dependency_spec!r}"
        )
        raise AssertionError(message)
    crate_name, dep_segment = parts
    dep_and_section = dep_segment.split("@", maxsplit=1)
    if len(dep_and_section) != 2:
        message = (
            "Dependency specification must contain exactly one '@' separator: "
            f"{dependency_spec!r}"
        )
        raise AssertionError(message)
    dependency_name, section = dep_and_section
    then_dependency_requirement(
        cli_run,
        DependencyCheck(
            crate_name=crate_name,
            dependency_name=dependency_name,
            section=section,
            expected_requirement=expected,
        ),
    )


# Import subcommand-specific steps so their definitions register with pytest-bdd.
from . import test_bump_steps as _bump_steps  # noqa: E402,F401  # isort: skip
from . import test_publish_steps as _publish_steps  # noqa: E402,F401  # isort: skip
