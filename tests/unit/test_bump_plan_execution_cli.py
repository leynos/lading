"""Command-level evidence that a validated bump plan executes exactly once.

These cases drive ``lading.cli`` in process and assert the successful write,
dry-run, root-member, and overlapping-target behaviour of the committed plan.
"""

import typing as typ
from pathlib import Path

from lading.commands import bump_toml
from tests.helpers.bump_cli_workspace import (
    WorkspaceOptions,
    create_cli_workspace,
)
from tests.helpers.fs_snapshot import changed_paths, snapshot_tree
from tests.helpers.recording_runner import RecordingCommandRunner
from tests.unit.bump_cli_harness import CliSeams, run_bump

if typ.TYPE_CHECKING:
    import pytest
    from _pytest.monkeypatch import MonkeyPatch


def test_live_bump_writes_and_regenerates_as_planned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A confined workspace bumps, and discovery used the exact Cargo flags."""
    workspace = create_cli_workspace(tmp_path / "workspace")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = run_bump(
        workspace, runner, CliSeams(monkeypatch, capsys)
    )

    assert exit_code == 0, f"a confined bump must succeed; stderr:\n{stderr}"
    assert "Updated version to 1.2.3" in stdout, (
        "the live run must report the applied version"
    )
    expected_metadata_calls = [
        ("cargo", "metadata", "--format-version", "1", "--no-deps"),
        (
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--no-deps",
            "--manifest-path",
            str(workspace.root / "Cargo.toml"),
        ),
    ]
    assert runner.metadata_calls() == expected_metadata_calls, (
        "discovery must pass --no-deps, then confirm the root for lockfile planning"
    )
    assert [command[:4] for command in runner.update_calls()] == [
        ("cargo", "update", "--workspace", "--manifest-path")
    ], "the live run must regenerate the planned root lockfile"
    for manifest in (
        workspace.root / "Cargo.toml",
        *workspace.crate_manifests.values(),
    ):
        assert 'version = "1.2.3"' in manifest.read_text(encoding="utf-8"), (
            f"{manifest} must carry the bumped version"
        )


def test_dry_run_reports_the_plan_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A dry run reports the same targets and leaves the workspace unchanged."""
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("README.md",)),
    )
    (workspace.root / "README.md").write_text(
        '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n', encoding="utf-8"
    )
    before = snapshot_tree(workspace.root)
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = run_bump(
        workspace,
        runner,
        CliSeams(monkeypatch, capsys),
        extra_args=("--dry-run",),
    )

    assert exit_code == 0, f"a dry run must succeed; stderr:\n{stderr}"
    assert "Dry run;" in stdout, "the dry run must label its summary"
    assert "- README.md (documentation)" in stdout, (
        "the dry run must name the documentation target it would rewrite"
    )
    assert not changed_paths(before, snapshot_tree(workspace.root)), (
        "a dry run must not modify any file"
    )
    assert runner.update_calls() == [], "a dry run must not regenerate lockfiles"


def _record_writes(monkeypatch: MonkeyPatch) -> list[Path]:
    """Record each atomic write while preserving the real write."""
    writes: list[Path] = []
    real_write = bump_toml.write_atomic_text

    def record(path: Path, content: str) -> None:
        """Record one write and then perform it."""
        writes.append(path)
        real_write(path, content)

    monkeypatch.setattr(bump_toml, "write_atomic_text", record)
    return writes


def test_root_member_manifest_is_written_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A root package that is also the sole member is reported once."""
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(members=(), root_package=True),
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    writes = _record_writes(monkeypatch)

    exit_code, stdout, stderr = run_bump(
        workspace, runner, CliSeams(monkeypatch, capsys)
    )

    assert exit_code == 0, f"a root-member bump must succeed; stderr:\n{stderr}"
    assert writes.count(workspace.root / "Cargo.toml") == 1, (
        "the root member manifest must be written exactly once"
    )
    root_text = (workspace.root / "Cargo.toml").read_text(encoding="utf-8")
    assert root_text.count('version = "1.2.3"') == 2, (
        "both the package and workspace.package versions must be updated"
    )
    manifest_lines = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip().startswith("- ") and line.strip().endswith("Cargo.toml")
    ]
    assert manifest_lines == ["- Cargo.toml"], (
        "the root member must be reported as a single manifest change"
    )


def test_overlapping_documentation_and_readme_target_is_reported_twice(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """One path targeted by both stages yields one write and two report lines."""
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(
            readme_workspace=True,
            documentation_globs=("README.md", "crates/*/README.md"),
            workspace_readme=(
                '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n\n'
                "See [the guide](../guide.md).\n"
            ),
        ),
    )
    crate_readme = workspace.root / "crates" / "alpha" / "README.md"
    crate_readme.write_text(
        '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n', encoding="utf-8"
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    writes = _record_writes(monkeypatch)

    exit_code, stdout, stderr = run_bump(
        workspace, runner, CliSeams(monkeypatch, capsys)
    )

    assert exit_code == 0, f"the overlapping targets must succeed; stderr:\n{stderr}"
    assert writes.count(crate_readme) == 1, (
        "the overlapping target must be written exactly once"
    )
    assert "- crates/alpha/README.md (documentation)" in stdout, (
        "the documentation category must be reported"
    )
    assert "- crates/alpha/README.md (readme)" in stdout, (
        "the README category must be reported for the same path"
    )
    assert 'alpha = "1.2.3"' in crate_readme.read_text(encoding="utf-8"), (
        "the adopted README must compose from the documentation rewrite"
    )


def test_an_unchanged_readme_adoption_is_not_reported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """An adoption that copies the already-rewritten text adds no category."""
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(
            readme_workspace=True,
            documentation_globs=("README.md", "crates/*/README.md"),
            workspace_readme='```toml\n[dependencies]\nalpha = "0.1.0"\n```\n',
        ),
    )
    crate_readme = workspace.root / "crates" / "alpha" / "README.md"
    crate_readme.write_text(
        '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n', encoding="utf-8"
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    writes = _record_writes(monkeypatch)

    exit_code, stdout, stderr = run_bump(
        workspace, runner, CliSeams(monkeypatch, capsys)
    )

    assert exit_code == 0, f"the overlapping targets must succeed; stderr:\n{stderr}"
    assert writes.count(crate_readme) == 1, (
        "the overlapping target must be written exactly once"
    )
    assert "- crates/alpha/README.md (documentation)" in stdout, (
        "the documentation category must be reported"
    )
    assert "- crates/alpha/README.md (readme)" not in stdout, (
        "an adoption that changes no text must not be reported"
    )
    assert 'alpha = "1.2.3"' in crate_readme.read_text(encoding="utf-8"), (
        "the documentation rewrite must still be written"
    )
