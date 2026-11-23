"""Command-level evidence that a rejected bump plan mutates nothing.

Every rejection case orders a valid member before the invalid input and drives
the real ``lading.cli`` in process through a recording runner. The assertions
compare a non-following snapshot of the workspace and of an external sentinel,
so a rule that rejected everything could not pass, and no temporary file or
lockfile can appear unnoticed.
"""

import collections.abc as cabc
import errno
import os
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from tests.helpers.bump_cli_workspace import (
    CliWorkspace,
    WorkspaceOptions,
    create_cli_workspace,
)
from tests.helpers.fs_snapshot import changed_paths, snapshot_tree
from tests.helpers.recording_runner import RecordingCommandRunner

if typ.TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

_ESCAPING_MEMBERS = [
    pytest.param("../outside", id="parent-traversal"),
    pytest.param(f"{os.sep}outside-409", id="absolute"),
    pytest.param("C:/outside", id="drive-qualified"),
]

_ESCAPING_GLOBS = [
    pytest.param("../outside/README.md", id="parent-traversal"),
    pytest.param("docs/../../outside/README.md", id="nested-traversal"),
]

_ESCAPING_LOCKFILES = [
    pytest.param("../outside/Cargo.toml", id="parent-traversal"),
    pytest.param(f"{os.sep}outside-409{os.sep}Cargo.toml", id="absolute"),
]


def _run_bump(
    workspace: CliWorkspace,
    runner: RecordingCommandRunner,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    extra_args: cabc.Sequence[str] = (),
) -> tuple[int, str, str]:
    """Run ``lading bump`` in process and return status, stdout, and stderr."""
    runner.install(monkeypatch)
    capsys.readouterr()
    exit_code = cli.main([
        "--workspace-root",
        str(workspace.root),
        "bump",
        "1.2.3",
        *extra_args,
    ])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _outside_sentinel(tmp_path: Path) -> Path:
    """Create and return an external sentinel file outside the workspace."""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    sentinel = outside / "README.md"
    sentinel.write_text("external\n", encoding="utf-8")
    return sentinel


def _assert_rejected(
    *,
    workspace: CliWorkspace,
    runner: RecordingCommandRunner,
    run: cabc.Callable[[], tuple[int, str, str]],
    expected: str,
) -> None:
    """Run ``run`` and assert a clean preflight rejection."""
    outside = workspace.root.parent / "outside"
    sentinel = outside / "README.md"
    before_workspace = snapshot_tree(workspace.root)
    before_sentinel = snapshot_tree(outside)
    exit_code, stdout, stderr = run()

    assert exit_code == 1, f"a rejected plan must exit 1; stderr:\n{stderr}"
    assert "Preflight error:" in stderr, (
        f"the diagnostic must use the preflight prefix; stderr:\n{stderr}"
    )
    assert expected in stderr, (
        f"the diagnostic must name {expected!r}; stderr:\n{stderr}"
    )
    assert not stdout, "a rejected plan must not write a summary"
    assert not changed_paths(before_workspace, snapshot_tree(workspace.root)), (
        "a rejected plan must leave the workspace byte-for-byte unchanged"
    )
    assert not changed_paths(before_sentinel, snapshot_tree(outside)), (
        "a rejected plan must leave the external sentinel unchanged"
    )
    assert sentinel.read_text(encoding="utf-8") == "external\n", (
        "the sentinel text must be untouched"
    )
    assert not (workspace.root / "Cargo.lock").exists(), (
        "a rejected plan must not create a lockfile"
    )
    assert runner.update_calls() == [], "a rejected plan must not run cargo update"


@pytest.mark.parametrize("member_pattern", _ESCAPING_MEMBERS)
def test_bump_rejects_an_unsafe_member_pattern_before_cargo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    member_pattern: str,
) -> None:
    """An unsafe member pattern is rejected with no metadata call."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    root_manifest = workspace.root / "Cargo.toml"
    root_manifest.write_text(
        f'[workspace]\nmembers = ["crates/alpha", "{member_pattern}"]\n\n'
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected=member_pattern,
    )
    assert runner.metadata_calls() == [], (
        "an unsafe member pattern must be rejected before Cargo runs"
    )


def test_bump_rejects_a_missing_literal_member(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A literal member pattern naming no directory is rejected."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    (workspace.root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/alpha", "crates/missing"]\n\n'
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="crates/missing",
    )
    assert runner.metadata_calls() == [], (
        "a missing literal member must be rejected before Cargo runs"
    )


def test_bump_rejects_a_symlinked_member_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A member directory that is a symlink is rejected before Cargo."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    (workspace.root / "crates" / "linked").symlink_to(
        tmp_path / "outside", target_is_directory=True
    )
    (workspace.root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/alpha", "crates/linked"]\n\n'
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="crates/linked",
    )
    assert runner.metadata_calls() == [], (
        "a symlinked member directory must be rejected before Cargo runs"
    )


@pytest.mark.parametrize("glob_pattern", _ESCAPING_GLOBS)
def test_bump_rejects_an_escaping_documentation_glob(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    glob_pattern: str,
) -> None:
    """An escaping documentation glob is rejected after manifests are planned."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=(glob_pattern,)),
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected=glob_pattern,
    )


def test_bump_rejects_a_symlinked_documentation_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A documentation glob matching a symlink is rejected."""
    sentinel = _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("docs/*.md",)),
    )
    (workspace.root / "docs").mkdir()
    (workspace.root / "docs" / "linked.md").symlink_to(sentinel)
    (workspace.root / "docs" / "real.md").write_text("", encoding="utf-8")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="docs/linked.md",
    )


def test_bump_rejects_a_symlinked_readme_destination(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """An adopted README whose destination is a symlink is rejected."""
    sentinel = _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(
            readme_workspace=True,
            workspace_readme="# Workspace README\n",
        ),
    )
    crate_readme = workspace.root / "crates" / "alpha" / "README.md"
    crate_readme.symlink_to(sentinel)
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="crates/alpha/README.md",
    )


def test_bump_reports_an_unreadable_documentation_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A selected documentation file that cannot be read is a preflight error."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("docs/*.md",)),
    )
    docs = workspace.root / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("", encoding="utf-8")
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    real_scandir = os.scandir
    target = docs.resolve()

    def failing_scandir(
        path: str | os.PathLike[str],
    ) -> cabc.Iterator[os.DirEntry[str]]:
        """Fail as an unreadable directory for the selected docs directory."""
        if os.fspath(path) == str(target):
            raise PermissionError(errno.EACCES, "denied", str(target))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="cannot list directory",
    )


@pytest.mark.parametrize("lockfile_manifest", _ESCAPING_LOCKFILES)
def test_bump_rejects_an_invalid_lockfile_manifest_before_any_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    lockfile_manifest: str,
) -> None:
    """An invalid configured lockfile manifest fails before every write."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(lockfile_manifests=(lockfile_manifest,)),
    )
    runner = RecordingCommandRunner(
        metadata=workspace.metadata,
        metadata_by_manifest=lambda manifest: {"workspace_root": str(tmp_path)},
    )

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected=lockfile_manifest,
    )
    assert runner.update_calls() == [], (
        "an invalid lockfile plan must not run cargo update"
    )


def test_bump_rejects_the_lockfile_path_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """An inherited Cargo lockfile-path override is rejected."""
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    monkeypatch.setenv("CARGO_RESOLVER_LOCKFILE_PATH", str(tmp_path / "elsewhere"))

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="CARGO_RESOLVER_LOCKFILE_PATH",
    )


def test_live_bump_writes_and_regenerates_as_planned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A confined workspace bumps, and discovery used the exact Cargo flags."""
    workspace = create_cli_workspace(tmp_path / "workspace")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = _run_bump(workspace, runner, monkeypatch, capsys)

    assert exit_code == 0, f"a confined bump must succeed; stderr:\n{stderr}"
    assert "Updated version to 1.2.3" in stdout, (
        "the live run must report the applied version"
    )
    assert runner.metadata_calls() == [
        ("cargo", "metadata", "--format-version", "1", "--no-deps")
    ], "bump discovery must pass --no-deps and nothing else"
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

    exit_code, stdout, stderr = _run_bump(
        workspace, runner, monkeypatch, capsys, extra_args=("--dry-run",)
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

    exit_code, stdout, stderr = _run_bump(workspace, runner, monkeypatch, capsys)

    assert exit_code == 0, f"a root-member bump must succeed; stderr:\n{stderr}"
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


def test_bump_rejects_a_special_documentation_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A FIFO matched by a documentation glob is rejected without opening it."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    _outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("docs/*.md",)),
    )
    docs = workspace.root / "docs"
    docs.mkdir()
    try:
        os.mkfifo(docs / "pipe.md")
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    _assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: _run_bump(workspace, runner, monkeypatch, capsys),
        expected="special file",
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
            workspace_readme='```toml\n[dependencies]\nalpha = "0.1.0"\n```\n',
        ),
    )
    crate_readme = workspace.root / "crates" / "alpha" / "README.md"
    crate_readme.write_text(
        '```toml\n[dependencies]\nalpha = "0.1.0"\n```\n', encoding="utf-8"
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = _run_bump(workspace, runner, monkeypatch, capsys)

    assert exit_code == 0, f"the overlapping targets must succeed; stderr:\n{stderr}"
    assert "- crates/alpha/README.md (documentation)" in stdout, (
        "the documentation category must be reported"
    )
    assert "- crates/alpha/README.md (readme)" in stdout, (
        "the README category must be reported for the same path"
    )
    assert 'alpha = "1.2.3"' in crate_readme.read_text(encoding="utf-8"), (
        "the adopted README must compose from the documentation rewrite"
    )
