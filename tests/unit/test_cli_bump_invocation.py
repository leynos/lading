"""End-to-end CLI checks for the bump target version and workspace override.

These drive ``lading.cli.main`` rather than the command function, so a
regression in argument plumbing between the Cyclopts app and ``bump.run`` is
visible. The version test proves an extended SemVer target is accepted and
written verbatim; the override test runs from an unrelated directory so only
``--workspace-root`` can explain a successful bump.
"""

import typing as typ
from pathlib import Path

from lading.commands import bump_toml
from tests.helpers.bump_cli_workspace import create_cli_workspace
from tests.helpers.recording_runner import RecordingCommandRunner
from tests.unit.bump_cli_harness import CliSeams, RunOptions, run_bump

if typ.TYPE_CHECKING:
    import pytest


def test_extended_semver_target_is_accepted_through_the_app(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prerelease and build-metadata targets reach the manifests unchanged."""
    workspace = create_cli_workspace(tmp_path / "workspace")
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    target = "1.2.3-alpha.1+build.5"

    exit_code, stdout, stderr = run_bump(
        workspace,
        runner,
        CliSeams(monkeypatch, capsys),
        options=RunOptions(target_version=target),
    )

    assert exit_code == 0, f"an extended SemVer target must be accepted:\n{stderr}"
    assert target in stdout, "the summary must name the applied target version"
    document = bump_toml.parse_manifest(workspace.crate_manifests["alpha"])
    assert document["package"]["version"] == target, (
        "the member manifest must carry the target version verbatim"
    )


def test_workspace_override_selects_the_named_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bump run from elsewhere operates on the overridden workspace only."""
    workspace = create_cli_workspace(tmp_path / "workspace")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = run_bump(
        workspace,
        runner,
        CliSeams(monkeypatch, capsys),
        options=RunOptions(extra_args=("--dry-run",)),
    )

    assert exit_code == 0, (
        f"the override must supply the workspace root from an unrelated cwd:\n{stderr}"
    )
    assert "1.2.3" in stdout, "the dry run must report the overridden workspace plan"
    document = bump_toml.parse_manifest(workspace.crate_manifests["alpha"])
    assert document["package"]["version"] == "0.1.0", (
        "a dry run must leave the overridden workspace unchanged"
    )
