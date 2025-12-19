"""Shared helpers for pytest-bdd end-to-end steps."""

from __future__ import annotations

import json
import os
import sys
import typing as typ
from pathlib import Path

from plumbum import local
from tomlkit.items import InlineTable, Item, Table

if typ.TYPE_CHECKING:  # pragma: no cover
    from cmd_mox import CmdMox

    from tests.e2e.helpers import workspace_builder


class _CmdMoxInvocation(typ.Protocol):
    args: typ.Sequence[str]
    env: typ.Mapping[str, str]


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


def run_cli(repo_root: Path, workspace_root: Path, *args: str) -> dict[str, typ.Any]:
    """Execute the lading CLI module and capture the result."""
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


def extract_dependency_requirement(entry: object) -> str:
    """Return a version requirement string from the manifest dependency entry."""
    match entry:
        case Item() as item if isinstance(item.value, str):
            return item.value
        case str() as value:
            return value
        case InlineTable() | Table() as table:
            return extract_dependency_requirement(table.get("version"))
        case _:
            raise E2EExpectationError.dependency_entry_not_string(entry)


def stub_cargo_metadata(
    cmd_mox: CmdMox, workspace: workspace_builder.NonTrivialWorkspace
) -> None:
    """Stub `cargo metadata` so the CLI can construct its workspace model."""
    cmd_mox.stub("cargo").with_args("metadata", "--format-version", "1").returns(
        exit_code=0,
        stdout=json.dumps(dict(workspace.cargo_metadata_payload)),
        stderr="",
    ).any_order()


def find_staging_root(stdout: str) -> Path:
    """Parse the publish CLI output and return the staging root directory."""
    for line in stdout.splitlines():
        if line.startswith("Staged workspace at: "):
            return Path(line.partition(": ")[2].strip())
    raise E2EExpectationError.staging_root_missing()


def filter_records(
    publish_spies: dict[str, typ.Any], label: str
) -> list[tuple[str, tuple[str, ...], dict[str, str]]]:
    """Return invocation records matching the given label."""
    return [record for record in publish_spies["records"] if record[0] == label]
