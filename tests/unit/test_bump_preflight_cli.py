"""Command-level evidence that a rejected bump plan mutates nothing.

Every rejection case orders a valid member before the invalid input and drives
the real ``lading.cli`` in process through a recording runner. The assertions
compare a non-following snapshot of the workspace and of an external sentinel,
so a rule that rejected everything could not pass, and no temporary file or
lockfile can appear unnoticed.
"""

import collections.abc as cabc
import errno
import json
import os
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.bump_cli_workspace import (
    CliWorkspace,
    WorkspaceOptions,
    create_cli_workspace,
)
from tests.helpers.recording_runner import RecordingCommandRunner
from tests.unit.bump_cli_harness import (
    CliSeams,
    RunOptions,
    assert_rejected,
    outside_sentinel,
    run_bump,
)

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


@pytest.fixture
def seams(
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> CliSeams:
    """Bundle the CLI seams for one test."""
    return CliSeams(monkeypatch, capsys)


@pytest.mark.parametrize("member_pattern", _ESCAPING_MEMBERS)
def test_bump_rejects_an_unsafe_member_pattern_before_cargo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    member_pattern: str,
) -> None:
    """An unsafe member pattern is rejected with no metadata call."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    root_manifest = workspace.root / "Cargo.toml"
    root_manifest.write_text(
        "[workspace]\n"
        f'members = ["crates/alpha", {json.dumps(member_pattern)}]\n\n'
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
        expected=member_pattern,
    )
    assert runner.metadata_calls() == [], (
        "an unsafe member pattern must be rejected before Cargo runs"
    )


def _declare_members(workspace: CliWorkspace, *patterns: str) -> None:
    """Rewrite the root manifest to declare ``patterns`` as its members."""
    literal = ", ".join(json.dumps(pattern) for pattern in patterns)
    (workspace.root / "Cargo.toml").write_text(
        f"[workspace]\nmembers = [{literal}]\n\n"
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def _symlink_member(workspace: CliWorkspace, name: str, target: Path) -> None:
    """Create ``crates/<name>`` as a directory symlink to ``target``."""
    (workspace.root / "crates" / name).symlink_to(target, target_is_directory=True)


def _missing_literal_member(workspace: CliWorkspace, tmp_path: Path) -> str:
    """Declare a literal member that names no directory."""
    del tmp_path
    _declare_members(workspace, "crates/alpha", "crates/missing")
    return "crates/missing"


def _symlinked_member(
    workspace: CliWorkspace,
    tmp_path: Path,
) -> str:
    """Declare a member directory that is a symlink."""
    _symlink_member(workspace, "linked", tmp_path / "outside")
    _declare_members(workspace, "crates/alpha", "crates/linked")
    return "crates/linked"


@pytest.mark.parametrize(
    ("prepare", "expected"),
    [
        pytest.param(_missing_literal_member, "crates/missing", id="missing-literal"),
        pytest.param(_symlinked_member, "crates/linked", id="symlinked-member"),
    ],
)
def test_bump_rejects_an_invalid_member_before_cargo(
    tmp_path: Path,
    seams: CliSeams,
    prepare: cabc.Callable[[CliWorkspace, Path], str],
    expected: str,
) -> None:
    """An invalid member pattern is rejected before Cargo runs."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    assert prepare(workspace, tmp_path) == expected, (
        "the fixture must name the rejected member"
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, seams),
        expected=expected,
    )
    assert runner.metadata_calls() == [], (
        "an invalid member must be rejected before Cargo runs"
    )


_DOCUMENTATION_REJECTIONS = [
    *(
        pytest.param(
            WorkspaceOptions(documentation_globs=(glob_pattern,)),
            glob_pattern,
            id=f"glob-{index}",
        )
        for index, glob_pattern in enumerate([
            "../outside/README.md",
            "docs/../../outside/README.md",
        ])
    ),
    pytest.param(
        WorkspaceOptions(readme_workspace=True, workspace_readme=None),
        "Workspace README.md is required",
        id="missing-workspace-readme",
    ),
]


@pytest.mark.parametrize(("options", "expected"), _DOCUMENTATION_REJECTIONS)
def test_bump_rejects_a_documentation_or_readme_plan(
    tmp_path: Path,
    seams: CliSeams,
    options: WorkspaceOptions,
    expected: str,
) -> None:
    """An escaping glob or a missing adopted README is a rejected plan."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace", options=options)
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, seams),
        expected=expected,
    )


def test_bump_rejects_a_symlinked_documentation_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A documentation glob matching a symlink is rejected."""
    sentinel = outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("docs/*.md",)),
    )
    (workspace.root / "docs").mkdir()
    (workspace.root / "docs" / "linked.md").symlink_to(sentinel)
    (workspace.root / "docs" / "real.md").write_text("", encoding="utf-8")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
        expected="docs/linked.md",
    )


def test_bump_rejects_a_symlinked_readme_destination(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """An adopted README whose destination is a symlink is rejected."""
    sentinel = outside_sentinel(tmp_path)
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

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
        expected="crates/alpha/README.md",
    )


def test_bump_reports_an_unreadable_documentation_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A selected documentation file that cannot be read is a preflight error."""
    outside_sentinel(tmp_path)
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

    def run_with_failing_scandir() -> tuple[int, str, str]:
        """Run the CLI with the injected listing failure active only there."""
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "scandir", failing_scandir)
            return run_bump(workspace, runner, CliSeams(monkeypatch, capsys))

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=run_with_failing_scandir,
        expected="cannot list directory",
    )


def _documented_version(version: str) -> str:
    """Return a TOML fence documenting ``alpha`` at ``version``."""
    return f'```toml\n[dependencies]\nalpha = "{version}"\n```\n'


def test_a_same_version_run_rewrites_outdated_documentation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """Control: the valid plan the rejection withholds does rewrite the file."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("docs/*.md",)),
    )
    docs = workspace.root / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(_documented_version("0.0.9"), encoding="utf-8")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = run_bump(
        workspace,
        runner,
        CliSeams(monkeypatch, capsys),
        options=RunOptions(target_version="0.1.0"),
    )

    assert exit_code == 0, f"the valid plan must succeed; stderr:\n{stderr}"
    assert "Updated version to 0.1.0" in stdout, (
        "the run must report the applied version"
    )
    assert (docs / "guide.md").read_text(encoding="utf-8") == _documented_version(
        "0.1.0"
    ), "the control must show the documentation rewrite actually happens"


@pytest.mark.parametrize("lockfile_manifest", _ESCAPING_LOCKFILES)
def test_bump_rejects_an_invalid_lockfile_manifest_on_a_same_version_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    lockfile_manifest: str,
) -> None:
    """An invalid destination is rejected even when no manifest changes.

    Lockfile planning is read-only, so the configured manifest is validated
    whenever regeneration is enabled. A same-version run still rewrites an
    outdated documentation file, and that write must not happen before the
    invalid destination is refused.
    """
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(
            documentation_globs=("docs/*.md",),
            lockfile_manifests=(lockfile_manifest,),
        ),
    )
    docs = workspace.root / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(_documented_version("0.0.9"), encoding="utf-8")
    runner = RecordingCommandRunner(
        metadata=workspace.metadata,
        metadata_by_manifest=lambda manifest: {"workspace_root": str(tmp_path)},
    )

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(
            workspace,
            runner,
            CliSeams(monkeypatch, capsys),
            options=RunOptions(target_version="0.1.0"),
        ),
        expected=lockfile_manifest,
    )
    assert (docs / "guide.md").read_text(encoding="utf-8") == _documented_version(
        "0.0.9"
    ), "the outdated documentation file must not be rewritten"
    assert runner.update_calls() == [], (
        "an invalid lockfile plan must not run cargo update"
    )


def test_bump_skips_lockfile_validation_when_regeneration_is_disabled(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """``--no-rebuild-lockfiles`` keeps an invalid destination out of scope."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(lockfile_manifests=("../outside/Cargo.toml",)),
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    exit_code, stdout, stderr = run_bump(
        workspace,
        runner,
        CliSeams(monkeypatch, capsys),
        options=RunOptions(extra_args=("--no-rebuild-lockfiles",)),
    )

    assert exit_code == 0, f"the disabled rebuild must succeed; stderr:\n{stderr}"
    assert "Updated version to 1.2.3" in stdout, (
        "the bump must report the applied version"
    )
    assert runner.update_calls() == [], "a disabled rebuild must never run cargo update"


@pytest.mark.parametrize("lockfile_manifest", _ESCAPING_LOCKFILES)
def test_bump_rejects_an_invalid_lockfile_manifest_before_any_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    lockfile_manifest: str,
) -> None:
    """An invalid configured lockfile manifest fails before every write."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(lockfile_manifests=(lockfile_manifest,)),
    )
    runner = RecordingCommandRunner(
        metadata=workspace.metadata,
        metadata_by_manifest=lambda manifest: {"workspace_root": str(tmp_path)},
    )

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
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
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    runner = RecordingCommandRunner(metadata=workspace.metadata)
    monkeypatch.setenv("CARGO_RESOLVER_LOCKFILE_PATH", str(tmp_path / "elsewhere"))

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
        expected="CARGO_RESOLVER_LOCKFILE_PATH",
    )


def test_bump_rejects_a_special_documentation_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """A FIFO matched by a documentation glob is rejected without opening it."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    outside_sentinel(tmp_path)
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

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
        expected="special file",
    )


def test_bump_rejects_hard_linked_documentation_targets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """Two documentation names for one inode are rejected before planning."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(
        tmp_path / "workspace",
        options=WorkspaceOptions(documentation_globs=("docs/*.md",)),
    )
    docs = workspace.root / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("", encoding="utf-8")
    try:
        os.link(docs / "a.md", docs / "b.md")
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"hard links are unavailable here: {exc}")
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, CliSeams(monkeypatch, capsys)),
        expected="hard-linked target",
    )


def _final_manifest_symlink(workspace: CliWorkspace, tmp_path: Path) -> str:
    """Keep the valid member first and make a later member's manifest a link."""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    external = outside / "Cargo.toml"
    external.write_text(
        '[package]\nname = "beta"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    beta = workspace.root / "crates" / "beta"
    beta.mkdir()
    (beta / "Cargo.toml").symlink_to(external)
    _declare_members(workspace, "crates/alpha", "crates/beta")
    return "crates/beta/Cargo.toml"


def _dangling_member_link(workspace: CliWorkspace, tmp_path: Path) -> str:
    """Point a declared member at a directory that does not exist."""
    _symlink_member(workspace, "dangling", tmp_path / "missing")
    _declare_members(workspace, "crates/alpha", "crates/dangling")
    return "crates/dangling"


def _in_root_alias(workspace: CliWorkspace, tmp_path: Path) -> str:
    """Add an in-root symlink alias beside the real member directory."""
    del tmp_path
    _symlink_member(workspace, "alias", workspace.root / "crates" / "alpha")
    _declare_members(workspace, "crates/alpha", "crates/alias")
    return "crates/alias"


def _unmatched_member_glob(workspace: CliWorkspace, tmp_path: Path) -> str:
    """Declare a member glob that matches nothing."""
    del tmp_path
    _declare_members(workspace, "crates/alpha", "plugins/*")
    return "plugins/*"


@pytest.mark.parametrize(
    ("prepare", "expected"),
    [
        pytest.param(
            _final_manifest_symlink, "crates/beta/Cargo.toml", id="final-symlink"
        ),
        pytest.param(_dangling_member_link, "crates/dangling", id="dangling-link"),
        pytest.param(_in_root_alias, "crates/alias", id="in-root-alias"),
        pytest.param(_unmatched_member_glob, "plugins/*", id="unmatched-glob"),
    ],
)
def test_bump_rejects_a_linked_or_unmatched_member_before_cargo(
    tmp_path: Path,
    seams: CliSeams,
    prepare: cabc.Callable[[CliWorkspace, Path], str],
    expected: str,
) -> None:
    """A symlinked, dangling, aliased or unmatched member is a rejected plan."""
    outside_sentinel(tmp_path)
    workspace = create_cli_workspace(tmp_path / "workspace")
    assert prepare(workspace, tmp_path) == expected, (
        "the fixture must name the rejected member"
    )
    runner = RecordingCommandRunner(metadata=workspace.metadata)

    assert_rejected(
        workspace=workspace,
        runner=runner,
        run=lambda: run_bump(workspace, runner, seams),
        expected=expected,
    )
    assert runner.metadata_calls() == [], (
        "the member must be rejected before Cargo runs"
    )
