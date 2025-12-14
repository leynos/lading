"""Step definitions for end-to-end lading CLI scenarios."""

from __future__ import annotations

import json
import os
import sys
import typing as typ
from pathlib import Path

from plumbum import local
from pytest_bdd import given, parsers, then, when
from tomlkit.items import InlineTable, Item, Table

from lading.testing import toml_utils
from tests.e2e.helpers import git_helpers, workspace_builder

if typ.TYPE_CHECKING:  # pragma: no cover
    import pytest
    from cmd_mox import CmdMox


class _CmdMoxInvocation(typ.Protocol):
    args: typ.Sequence[str]
    env: typ.Mapping[str, str]


class _CmdMoxDouble(typ.Protocol):
    invocations: list[typ.Any]
    call_count: int

    def passthrough(self) -> _CmdMoxDouble: ...


class E2EExpectationError(AssertionError):
    """Raised when an end-to-end test expectation is violated."""

    @classmethod
    def unsupported_fixture_version(cls, version: str) -> E2EExpectationError:
        """Return an error for unsupported fixture versions."""
        return cls(f"E2E fixture currently supports version 0.1.0 only (got {version})")

    @classmethod
    def dependency_entry_not_string(cls, entry: object) -> E2EExpectationError:
        """Return an error when a TOML dependency entry is not a string-like version."""
        return cls(f"Dependency entry is not a version string: {entry!r}")

    @classmethod
    def args_prefix_mismatch(
        cls,
        label: str,
        expected_prefix: tuple[str, ...],
        args: tuple[str, ...],
    ) -> E2EExpectationError:
        """Return an error when recorded args do not match the expected prefix."""
        return cls(f"{label} expected args prefix {expected_prefix!r}, got {args!r}")

    @classmethod
    def target_dir_missing(
        cls, label: str, args: tuple[str, ...]
    ) -> E2EExpectationError:
        """Return an error when the pre-flight target dir flag is missing."""
        return cls(f"{label} expected --target-dir=... at args[2], got {args!r}")

    @classmethod
    def staging_root_missing(cls) -> E2EExpectationError:
        """Return an error when publish output lacks the staging root line."""
        return cls("publish output did not include staging root")


def _run_cli(repo_root: Path, workspace_root: Path, *args: str) -> dict[str, typ.Any]:
    with local.cwd(str(repo_root)):
        exit_code, stdout, stderr = local[sys.executable].run(
            ["-m", "lading.cli", "--workspace-root", str(workspace_root), *args],
            retcode=None,
            env=dict(os.environ),
        )
    return {
        "command": [
            sys.executable,
            "-m",
            "lading.cli",
            "--workspace-root",
            str(workspace_root),
            *args,
        ],
        "returncode": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "workspace_root": workspace_root,
    }


def _extract_dependency_requirement(entry: object) -> str:
    if isinstance(entry, Item):
        value = entry.value
        if isinstance(value, str):
            return value
    if isinstance(entry, str):
        return entry
    if isinstance(entry, InlineTable | Table):
        version_value = entry.get("version")
        return _extract_dependency_requirement(version_value)
    raise E2EExpectationError.dependency_entry_not_string(entry)


def _stub_cargo_metadata(
    cmd_mox: CmdMox, workspace: workspace_builder.NonTrivialWorkspace
) -> None:
    cmd_mox.mock("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=0,
        stdout=json.dumps(dict(workspace.cargo_metadata_payload)),
        stderr="",
    ).any_order()


@given(
    parsers.parse('a non-trivial workspace in a Git repository at version "{version}"'),
    target_fixture="e2e_state",
)
def given_nontrivial_workspace_in_git_repo(
    version: str,
    cmd_mox: CmdMox,
    monkeypatch: pytest.MonkeyPatch,
    e2e_git_repo: Path,
    e2e_workspace: workspace_builder.NonTrivialWorkspace,
) -> dict[str, typ.Any]:
    """Create a non-trivial workspace fixture and stub cargo metadata."""
    if version != "0.1.0":
        raise E2EExpectationError.unsupported_fixture_version(version)
    monkeypatch.setenv("LADING_USE_CMD_MOX_STUB", "1")
    _stub_cargo_metadata(cmd_mox, e2e_workspace)
    return {"workspace": e2e_workspace, "git_repo": e2e_git_repo}


@given(
    "cargo commands are stubbed for publish operations", target_fixture="publish_spies"
)
def given_cargo_commands_stubbed(
    cmd_mox: CmdMox,
    e2e_state: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Stub cargo pre-flight and publish loop commands; allow real git status."""
    git_spy = cmd_mox.spy("git").passthrough()
    invocation_records: list[tuple[str, tuple[str, ...], dict[str, str]]] = []

    def _recording_handler(
        label: str,
        expected_prefix: tuple[str, ...] = (),
        *,
        require_target_dir: bool = False,
    ) -> typ.Callable[[_CmdMoxInvocation], tuple[str, str, int]]:
        def _handler(invocation: _CmdMoxInvocation) -> tuple[str, str, int]:
            args = tuple(invocation.args)
            if expected_prefix and args[: len(expected_prefix)] != expected_prefix:
                raise E2EExpectationError.args_prefix_mismatch(
                    label, expected_prefix, args
                )
            if require_target_dir and (
                len(args) < 3 or not args[2].startswith("--target-dir=")
            ):
                raise E2EExpectationError.target_dir_missing(label, args)
            invocation_records.append((label, args, dict(invocation.env)))
            return ("", "", 0)

        return _handler

    cmd_mox.stub("cargo::check").runs(
        _recording_handler(
            "cargo::check",
            ("--workspace", "--all-targets"),
            require_target_dir=True,
        )
    )
    cmd_mox.stub("cargo::test").runs(
        _recording_handler(
            "cargo::test",
            ("--workspace", "--all-targets"),
            require_target_dir=True,
        )
    )
    cmd_mox.stub("cargo::package").runs(_recording_handler("cargo::package"))
    cmd_mox.stub("cargo::publish").runs(
        _recording_handler("cargo::publish", ("--dry-run",))
    )

    return {
        "git_spy": git_spy,
        "records": invocation_records,
        "workspace": e2e_state["workspace"],
    }


@when(
    parsers.parse('I run lading bump "{version}" in the E2E workspace'),
    target_fixture="cli_run",
)
def when_run_lading_bump(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
    version: str,
) -> dict[str, typ.Any]:
    """Invoke `lading bump` against the E2E workspace and capture output."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    return _run_cli(repo_root, workspace.root, "bump", version)


@when(
    "I run lading publish --forbid-dirty in the E2E workspace", target_fixture="cli_run"
)
def when_run_lading_publish(
    repo_root: Path,
    e2e_state: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Invoke `lading publish` (dry-run default) with `--forbid-dirty`."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    return _run_cli(repo_root, workspace.root, "publish", "--forbid-dirty")


@then("the command succeeds")
def then_command_succeeds(cli_run: dict[str, typ.Any]) -> None:
    """Assert the CLI exited successfully."""
    assert cli_run["returncode"] == 0, cli_run["stderr"]


@then('all workspace manifests are at version "1.0.0"')
def then_manifests_at_version(e2e_state: dict[str, typ.Any]) -> None:
    """Assert the workspace and member crate versions match the expected value."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    root_doc = toml_utils.load_manifest(workspace.root / "Cargo.toml")
    assert root_doc["workspace"]["package"]["version"] == "1.0.0"
    for name in workspace.crate_names:
        crate_doc = toml_utils.load_manifest(
            workspace.root / "crates" / name / "Cargo.toml"
        )
        assert crate_doc["package"]["version"] == "1.0.0"


@then('internal dependency versions are updated to "1.0.0"')
def then_internal_dependencies_updated(e2e_state: dict[str, typ.Any]) -> None:
    """Assert internal dependency version requirements reflect the new version."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    utils_doc = toml_utils.load_manifest(
        workspace.root / "crates" / "utils" / "Cargo.toml"
    )
    assert (
        _extract_dependency_requirement(utils_doc["dependencies"]["core"]) == "^1.0.0"
    )
    assert (
        _extract_dependency_requirement(utils_doc["dev-dependencies"]["core"])
        == "~1.0.0"
    )
    app_doc = toml_utils.load_manifest(workspace.root / "crates" / "app" / "Cargo.toml")
    assert _extract_dependency_requirement(app_doc["dependencies"]["core"]) == "1.0.0"
    assert _extract_dependency_requirement(app_doc["dependencies"]["utils"]) == "~1.0.0"
    assert (
        _extract_dependency_requirement(app_doc["build-dependencies"]["core"])
        == "1.0.0"
    )


@then('the workspace README contains version "1.0.0"')
def then_readme_contains_version(e2e_state: dict[str, typ.Any]) -> None:
    """Assert the workspace README TOML snippet reflects the bumped version."""
    workspace: workspace_builder.NonTrivialWorkspace = e2e_state["workspace"]
    readme = (workspace.root / "README.md").read_text(encoding="utf-8")
    assert 'core = "1.0.0"' in readme
    assert 'utils = "1.0.0"' in readme
    assert 'app = "1.0.0"' in readme


@then("the Git working tree has uncommitted changes")
def then_git_dirty(e2e_state: dict[str, typ.Any]) -> None:
    """Assert `git status --porcelain` reports modifications in the workspace."""
    repo_root: Path = e2e_state["git_repo"]
    status = git_helpers.git_status_porcelain(repo_root)
    assert status.strip(), "expected a dirty git status"


def _find_staging_root(stdout: str) -> Path:
    """Parse the publish CLI output and return the staging root directory."""
    for line in stdout.splitlines():
        if line.startswith("Staged workspace at: "):
            return Path(line.partition(": ")[2].strip())
    raise E2EExpectationError.staging_root_missing()


@then(parsers.parse('the publish order is "{expected}"'))
def then_publish_order(publish_spies: dict[str, typ.Any], expected: str) -> None:
    """Assert cargo package calls occur in the expected crate order."""
    expected_names = [name.strip() for name in expected.split(",") if name.strip()]
    package_calls = [
        record for record in publish_spies["records"] if record[0] == "cargo::package"
    ]
    seen = []
    for _label, _args, env in package_calls:
        cwd = Path(env["PWD"])
        seen.append(cwd.name)
    assert seen == expected_names


@then("cargo package was invoked for each crate")
def then_cargo_package_invoked(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo package was invoked once per crate."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    package_calls = [
        record for record in publish_spies["records"] if record[0] == "cargo::package"
    ]
    assert len(package_calls) == len(workspace.crate_names)
    called = {Path(env["PWD"]).name for _label, _args, env in package_calls}
    assert called == set(workspace.crate_names)


@then("cargo publish --dry-run was invoked for each crate")
def then_cargo_publish_invoked(publish_spies: dict[str, typ.Any]) -> None:
    """Assert cargo publish --dry-run was invoked once per crate."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    publish_calls = [
        record for record in publish_spies["records"] if record[0] == "cargo::publish"
    ]
    assert len(publish_calls) == len(workspace.crate_names)
    called = {Path(env["PWD"]).name for _label, _args, env in publish_calls}
    assert called == set(workspace.crate_names)


@then("the workspace README was staged for all crates")
def then_readme_staged(
    cli_run: dict[str, typ.Any],
    publish_spies: dict[str, typ.Any],
    staging_cleanup: typ.Callable[[Path], None],
) -> None:
    """Assert publish staging copied the workspace README into each crate."""
    workspace: workspace_builder.NonTrivialWorkspace = publish_spies["workspace"]
    staging_root = _find_staging_root(cli_run["stdout"])
    try:
        expected_paths = [
            staging_root / "crates" / name / "README.md"
            for name in workspace.crate_names
        ]
        for path in expected_paths:
            assert path.exists(), f"expected staged README: {path}"
    finally:
        staging_cleanup(staging_root)

    copied_lines = [
        line.strip()
        for line in cli_run["stdout"].splitlines()
        if line.strip().startswith("- crates/")
    ]
    assert len(copied_lines) == len(workspace.crate_names)
