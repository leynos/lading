"""Shared helpers for pytest-bdd end-to-end steps."""

from __future__ import annotations

import collections.abc as cabc
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
    args: cabc.Sequence[str]
    env: cabc.Mapping[str, str]


class E2EExpectationError(AssertionError):
    """Raised when an end-to-end test expectation is violated."""

    @classmethod
    def unsupported_fixture_version(cls, version: str) -> E2EExpectationError:
        """Return an error for unsupported fixture versions.

        Parameters
        ----------
        version : str
            The unsupported fixture version that was requested.

        Returns
        -------
        E2EExpectationError
            The error describing the unsupported version.
        """
        return cls(f"E2E fixture currently supports version 0.1.0 only (got {version})")

    @classmethod
    def dependency_entry_not_string(cls, entry: object) -> E2EExpectationError:
        """Return an error when a TOML dependency entry is not a string version.

        Parameters
        ----------
        entry : object
            The TOML dependency entry that was not a version string.

        Returns
        -------
        E2EExpectationError
            The error describing the offending entry.
        """
        return cls(f"Dependency entry is not a version string: {entry!r}")

    @classmethod
    def args_prefix_mismatch(
        cls,
        label: str,
        expected_prefixes: tuple[tuple[str, ...], ...],
        args: tuple[str, ...],
    ) -> E2EExpectationError:
        """Return an error when recorded args do not match the expected prefix(es).

        Parameters
        ----------
        label : str
            The command label whose recorded arguments were checked.
        expected_prefixes : tuple[tuple[str, ...], ...]
            The accepted argument prefixes.
        args : tuple[str, ...]
            The recorded arguments that failed to match a prefix.

        Returns
        -------
        E2EExpectationError
            The error describing the mismatched args.
        """
        expected = ", ".join(repr(prefix) for prefix in expected_prefixes)
        return cls(f"{label} expected args prefix in ({expected}), got {args!r}")

    @classmethod
    def target_dir_missing(
        cls, label: str, args: tuple[str, ...]
    ) -> E2EExpectationError:
        """Return an error when the pre-flight target dir flag is missing.

        Returns
        -------
        E2EExpectationError
            The error describing the missing flag.
        """
        return cls(f"{label} expected --target-dir=... in args, got {args!r}")

    @classmethod
    def staging_root_missing(cls) -> E2EExpectationError:
        """Return an error when publish output lacks the staging root line.

        Returns
        -------
        E2EExpectationError
            The error describing the missing staging root.
        """
        return cls("publish output did not include staging root")


def run_cli(repo_root: Path, workspace_root: Path, *args: str) -> dict[str, typ.Any]:
    """Execute the lading CLI module and capture the result.

    Parameters
    ----------
    repo_root : Path
        Repository root used as the subprocess working directory.
    workspace_root : Path
        Workspace root passed to the CLI via ``--workspace-root``.
    *args : str
        Additional command-line arguments forwarded to ``lading.cli``.

    Returns
    -------
    dict[str, typ.Any]
        The command line, return code, captured stdout and stderr, and the
        workspace root.
    """
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
    """Return a version requirement string from the manifest dependency entry.

    Parameters
    ----------
    entry : object
        The manifest dependency entry (string, item, or table) to inspect.

    Returns
    -------
    str
        The version requirement extracted from the entry.

    Raises
    ------
    E2EExpectationError
        Constructed by
        ``E2EExpectationError.dependency_entry_not_string(entry)`` when the
        entry is neither a version string nor a table containing one.
    """
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
    cmd_mox.stub("cargo::update").with_args(
        "--workspace",
        "--manifest-path",
        str(workspace.root / "Cargo.toml"),
    ).returns(exit_code=0, stdout="", stderr="").any_order()


def find_staging_root(stdout: str) -> Path:
    """Parse the publish CLI output and return the staging root directory.

    Parameters
    ----------
    stdout : str
        The captured publish CLI standard output to parse.

    Returns
    -------
    Path
        The staging root directory parsed from the output.

    Raises
    ------
    E2EExpectationError
        Constructed by ``E2EExpectationError.staging_root_missing()`` when the
        output contains no staging-root line.
    """
    for line in stdout.splitlines():
        if line.startswith("Staged workspace at: "):
            return Path(line.partition(": ")[2].strip())
    raise E2EExpectationError.staging_root_missing()


def filter_records(
    publish_spies: dict[str, typ.Any], label: str
) -> list[tuple[str, tuple[str, ...], dict[str, str]]]:
    """Return invocation records matching the given label.

    Parameters
    ----------
    publish_spies : dict[str, typ.Any]
        The recorded cargo invocation spies keyed by command name.
    label : str
        The command label to filter invocation records by.

    Returns
    -------
    list[tuple[str, tuple[str, ...], dict[str, str]]]
        The invocation records whose label matches.
    """
    return [record for record in publish_spies["records"] if record[0] == label]
