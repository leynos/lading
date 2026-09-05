"""Given steps for publish BDD scenarios.

This module prepares the workspace, configuration, and command-runner state
used by publish feature scenarios. Its fixtures create representative Cargo
metadata, inject cmd-mox command responses, and write scenario-specific
`lading.toml` configuration so the behavioural tests can exercise the public
`lading publish` CLI through the same process boundary as a user.

The step definitions pair with `test_publish_when_steps` for command
execution, `test_publish_then_steps` for assertions, and
`test_publish_infrastructure` for shared command-spy plumbing. Keeping setup
steps here makes each feature scenario read as domain behaviour while the
implementation remains explicit about which Cargo or git command is being
simulated.
"""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers

from .metadata_fixtures import given_cargo_metadata_with_dependency_chain
from .test_publish_infrastructure import (
    CmdMox,
    ResponseProvider,
    _CmdInvocation,
    _CommandResponse,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    import pytest

try:
    from cmd_mox import CmdMox as _ImportedCmdMox
except ModuleNotFoundError:  # pragma: no cover - runtime fallback
    _ImportedCmdMox = CmdMox  # type: ignore[misc]


_INDEX_MISSING_STDERR_ALPHA = (
    "error: failed to prepare local package for uploading\n"
    "\n"
    "Caused by:\n"
    '  failed to select a version for the requirement `alpha = "^0.1.0"`\n'
    "  candidate versions found which didn't match: 0.0.1\n"
    "  location searched: crates.io index\n"
    "  required by package `beta v0.1.0`\n"
)


@given("cargo check fails during publish pre-flight")
def given_cargo_check_fails(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a failing cargo check command."""
    preflight_overrides["cargo", "check", "--workspace", "--all-targets"] = (
        _CommandResponse(exit_code=1, stderr="cargo check failed")
    )


@given("cargo test fails during publish pre-flight")
def given_cargo_test_fails(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a failing cargo test command."""
    preflight_overrides["cargo", "test", "--workspace"] = _CommandResponse(
        exit_code=1, stderr="cargo test failed"
    )


@given("publish pre-flight finds a stale tracked Cargo.lock")
def given_publish_preflight_finds_stale_lockfile(
    workspace_directory: Path,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a tracked lockfile that fails locked metadata validation."""
    (workspace_directory / "Cargo.lock").write_text("# stale lock\n", encoding="utf-8")
    preflight_overrides["git", "ls-files", "**/Cargo.lock", "Cargo.lock"] = (
        _CommandResponse(exit_code=0, stdout="Cargo.lock\n")
    )
    preflight_overrides[
        "cargo",
        "metadata",
        "--locked",
        "--manifest-path",
    ] = _CommandResponse(
        exit_code=101,
        stderr=(
            f"error: cannot update the lock file {workspace_directory / 'Cargo.lock'} "
            "because --locked was passed to prevent this"
        ),
    )


@given(
    "publish pre-flight probes a fresh tracked Cargo.lock with a large "
    "metadata document"
)
def given_publish_preflight_probes_fresh_lockfile_with_large_metadata(
    workspace_directory: Path,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a fresh lockfile whose probe returns a one-line metadata document.

    ``cargo metadata --locked`` prints the whole metadata document on one
    line, megabytes long for a real workspace. The stub answers with a
    sentinel-bearing single line so a scenario can assert that the probe's
    stdout is captured for diagnostics but never mirrored to the console
    (issue #251).
    """
    (workspace_directory / "Cargo.lock").write_text("# fresh lock\n", encoding="utf-8")
    preflight_overrides["git", "ls-files", "**/Cargo.lock", "Cargo.lock"] = (
        _CommandResponse(exit_code=0, stdout="Cargo.lock\n")
    )
    metadata_document = (
        '{"packages": [], "workspace_members": [], '
        '"marker": "LADING-METADATA-SENTINEL"}\n'
    )
    preflight_overrides[
        "cargo",
        "metadata",
        "--locked",
        "--manifest-path",
    ] = _CommandResponse(exit_code=0, stdout=metadata_document)


@given("publish pre-flight finds multiple stale tracked Cargo.lock files")
def given_publish_preflight_finds_multiple_stale_lockfiles(
    workspace_directory: Path,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate several tracked lockfiles that all fail locked validation.

    Two tracked lockfiles (the workspace root and a nested ``sub`` crate) are
    staged on disk with adjacent manifests so discovery returns both, and a
    single prefix-matched ``cargo metadata --locked`` stub reports each as
    stale. This exercises the aggregated, no-short-circuit stale report.
    """
    nested = workspace_directory / "sub"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (workspace_directory / "Cargo.lock").write_text("# stale lock\n", encoding="utf-8")
    (nested / "Cargo.lock").write_text("# stale lock\n", encoding="utf-8")
    preflight_overrides["git", "ls-files", "**/Cargo.lock", "Cargo.lock"] = (
        _CommandResponse(exit_code=0, stdout="Cargo.lock\nsub/Cargo.lock\n")
    )
    preflight_overrides[
        "cargo",
        "metadata",
        "--locked",
        "--manifest-path",
    ] = _CommandResponse(
        exit_code=101,
        stderr=(
            "error: cannot update the lock file because --locked was passed "
            "to prevent this"
        ),
    )


@given(parsers.parse('cargo test fails with compiletest artefact "{relative_path}"'))
def given_cargo_test_fails_with_artefact(
    workspace_directory: Path,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    relative_path: str,
) -> None:
    """Create ``relative_path`` and configure cargo test to reference it."""
    artefact = workspace_directory / relative_path
    artefact.parent.mkdir(parents=True, exist_ok=True)
    artefact.write_text("line1\nline2\n", encoding="utf-8")
    preflight_overrides["cargo", "test", "--workspace"] = _CommandResponse(
        exit_code=1,
        stderr=f"diff at {artefact}",
    )


@given(parsers.parse('cargo publish reports crate "{crate_name}" already uploaded'))
def given_cargo_publish_already_uploaded(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    crate_name: str,
) -> None:
    """Simulate cargo publish returning an already-uploaded error for ``crate_name``."""

    def _handler(invocation: _CmdInvocation) -> _CommandResponse:
        env_mapping = dict(getattr(invocation, "env", {}))
        if "PWD" not in env_mapping:
            message = (
                "cargo publish pre-flight stub expected PWD in the invocation "
                "environment"
            )
            raise AssertionError(message)
        cwd = Path(env_mapping["PWD"])
        if cwd.name == crate_name:
            error_message = (
                f"error: crate version `{crate_name} v0.1.0` is already uploaded"
            )
            return _CommandResponse(
                exit_code=101,
                stderr=error_message,
            )
        return _CommandResponse(exit_code=0)

    preflight_overrides["cargo", "publish", "--dry-run"] = _handler


@given("a workspace where a sibling crate dependency is not yet indexed")
def given_sibling_dependency_is_not_indexed(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Make cargo package fail for beta because alpha is not indexed yet."""

    def _handler(invocation: _CmdInvocation) -> _CommandResponse:
        env_mapping = dict(getattr(invocation, "env", {}))
        cwd = Path(env_mapping.get("PWD", ""))
        if cwd.name == "beta":
            return _CommandResponse(exit_code=1, stderr=_INDEX_MISSING_STDERR_ALPHA)
        return _CommandResponse(exit_code=0)

    preflight_overrides["cargo", "package"] = _handler


@given("the missing dependency is part of the planned publish set")
def given_missing_dependency_is_in_plan() -> None:
    """Document that the dependency-chain fixture includes alpha in the plan."""


@given("publish.order puts beta before alpha")
def given_publish_order_puts_beta_before_alpha(workspace_directory: Path) -> None:
    """Configure an explicit publish order where beta precedes alpha."""
    config_path = workspace_directory / "lading.toml"
    config_path.write_text(
        '[bump]\n\n[publish]\nstrip_patches = "all"\n'
        'order = ["beta", "alpha", "gamma"]\n',
        encoding="utf-8",
    )


@given("the workspace has uncommitted changes")
def given_workspace_dirty(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Simulate a dirty working tree for git status."""
    preflight_overrides["git", "status", "--porcelain"] = _CommandResponse(
        exit_code=0,
        stdout=" M Cargo.toml\n",
    )


@given(
    parsers.re(
        r'the preflight command "(?P<command>.+)" exits with '
        r'code (?P<exit_code>\d+) and stderr "(?P<stderr>.*)"'
    )
)
def given_preflight_command_override(
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    command: str,
    exit_code: str,
    stderr: str,
) -> None:
    """Override an arbitrary pre-flight command with a custom result.

    Parameters
    ----------
    preflight_overrides : dict[tuple[str, ...], ResponseProvider]
        Mapping of command tuples to stubbed responses, mutated in place.
    command : str
        Whitespace-separated command whose tokens key the override.
    exit_code : str
        Decimal exit code the stubbed command should return.
    stderr : str
        Standard-error text the stubbed command should emit.

    Raises
    ------
    AssertionError
        If ``command`` yields no tokens once split.
    """
    if tokens := tuple(segment for segment in command.split() if segment):
        preflight_overrides[tokens] = _CommandResponse(
            exit_code=int(exit_code),
            stderr=stderr,
        )
    else:
        message = "preflight command override requires tokens"
        raise AssertionError(message)


@given("RUSTC_WRAPPER names a stub sccache")
def given_rustc_wrapper_names_stub_sccache(
    monkeypatch: pytest.MonkeyPatch,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
) -> None:
    """Point ``RUSTC_WRAPPER`` at a stubbed ``sccache`` on the cmd-mox PATH.

    The JSON query answers with counters that grow by ten requests, eight
    hits, and two misses per call, so every per-crate delta is the same and
    the summary lines are predictable. The JSON override is registered before
    the plain ``--show-stats`` one because the stub dispatcher matches argument
    prefixes in registration order.
    """
    monkeypatch.setenv("RUSTC_WRAPPER", "sccache")
    queries = 0

    def _json_stats(_invocation: _CmdInvocation) -> _CommandResponse:
        nonlocal queries
        queries += 1
        payload = json.dumps({
            "stats": {
                "compile_requests": 10 * queries,
                "cache_hits": {"counts": {"Rust": 8 * queries}},
                "cache_misses": {"counts": {"Rust": 2 * queries}},
            },
            "version": "0.14.0",
        })
        return _CommandResponse(exit_code=0, stdout=payload)

    preflight_overrides["sccache", "--show-stats", "--stats-format=json"] = _json_stats
    preflight_overrides["sccache", "--show-stats"] = _CommandResponse(
        exit_code=0, stdout="Compile requests   70\nCache location   ghac\n"
    )


@given("RUSTC_WRAPPER is not set")
def given_rustc_wrapper_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no compiler wrapper is configured for the CLI subprocess."""
    monkeypatch.delenv("RUSTC_WRAPPER", raising=False)


@given("a valid lading workspace", target_fixture="workspace_directory")
def given_valid_lading_workspace(
    tmp_path: Path,
    cmd_mox: _ImportedCmdMox,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create a configured workspace with a publish dependency chain.

    Parameters
    ----------
    tmp_path : Path
        Pytest-provided temporary directory used as the workspace root.
    cmd_mox : CmdMox
        The cmd-mox controller that stubs cargo metadata queries.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to install the stubbed metadata behaviour.

    Returns
    -------
    Path
        The workspace root directory.
    """
    from lading import config as config_module

    config_path = tmp_path / config_module.CONFIG_FILENAME
    config_path.write_text(
        '[bump]\n\n[publish]\nstrip_patches = "all"\n', encoding="utf-8"
    )
    given_cargo_metadata_with_dependency_chain(cmd_mox, monkeypatch, tmp_path)
    return tmp_path
