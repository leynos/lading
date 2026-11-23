"""Shared harness for in-process bump preflight command tests.

Both the rejection suite and the successful-execution suite drive
``lading.cli`` with a recording runner. The helpers here keep the capture and
sentinel setup identical between them.
"""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

from lading import cli
from tests.helpers.fs_snapshot import changed_paths, snapshot_tree

if typ.TYPE_CHECKING:
    import pytest

    from tests.helpers.bump_cli_workspace import CliWorkspace
    from tests.helpers.recording_runner import RecordingCommandRunner


def outside_sentinel(tmp_path: Path) -> Path:
    r"""Create or overwrite the external sentinel beside the workspace.

    Parameters
    ----------
    tmp_path : Path
        Per-test directory; the sentinel is written to ``tmp_path/outside``.

    Returns
    -------
    Path
        The sentinel file, containing ``"external\n"``.
    """
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    sentinel = outside / "README.md"
    sentinel.write_text("external\n", encoding="utf-8")
    return sentinel


@dc.dataclass(frozen=True, slots=True)
class CliSeams:
    """The pytest fixtures a bump invocation needs.

    Attributes
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to install the recording runner.
    capsys : pytest.CaptureFixture[str]
        Fixture used to capture the invocation's streams.
    """

    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture[str]


def run_bump(
    workspace: CliWorkspace,
    runner: RecordingCommandRunner,
    seams: CliSeams,
    *,
    extra_args: cabc.Sequence[str] = (),
) -> tuple[int, str, str]:
    """Run ``lading bump`` in process and return status, stdout, and stderr.

    The runner is installed through ``lading.cli._select_runner`` and any
    pending capture is cleared before the invocation.

    Parameters
    ----------
    workspace : CliWorkspace
        Materialised workspace and metadata payload.
    runner : RecordingCommandRunner
        Runner to install for this invocation.
    seams : CliSeams
        Monkeypatch and capture fixtures, bundled to keep the call site small.
    extra_args : cabc.Sequence[str], optional
        Additional bump arguments, such as ``--dry-run``.

    Returns
    -------
    tuple[int, str, str]
        Exit status, stdout, and stderr.
    """
    runner.install(seams.monkeypatch)
    seams.capsys.readouterr()
    exit_code = cli.main([
        "--workspace-root",
        str(workspace.root),
        "bump",
        "1.2.3",
        *extra_args,
    ])
    captured = seams.capsys.readouterr()
    return exit_code, captured.out, captured.err


def assert_rejected(
    *,
    workspace: CliWorkspace,
    runner: RecordingCommandRunner,
    run: cabc.Callable[[], tuple[int, str, str]],
    expected: str,
) -> None:
    r"""Run ``run`` and assert a clean preflight rejection.

    Parameters
    ----------
    workspace : CliWorkspace
        Materialised workspace; its parent must hold the sentinel.
    runner : RecordingCommandRunner
        Runner the invocation uses, checked for absent update calls.
    run : cabc.Callable[[], tuple[int, str, str]]
        Invocation to execute.
    expected : str
        Text the diagnostic must name.
    """
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


__all__ = ["CliSeams", "assert_rejected", "outside_sentinel", "run_bump"]
