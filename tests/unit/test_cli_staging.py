"""Unit tests for the staging lifecycle at the command-line layer.

These live outside ``test_cli.py`` because they cover one thing: that the
staged workspace copy's lifetime is wired up where the application starts.
Two separate failures hide here, and both leave a working command. A
termination handler that bootstrap never installs restores issue #269 in
full, and a ``--keep-staging`` that does not reach ``PublishOptions`` either
deletes a copy someone was debugging or retains one nobody asked for.
"""

from __future__ import annotations

import typing as typ

import pytest

from lading import cli, cli_options
from lading.commands import publish as publish_command
from lading.workspace import WorkspaceCrate, WorkspaceGraph

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path


def _make_workspace(root: Path) -> WorkspaceGraph:
    """Return a one-crate workspace graph for these tests.

    Returns
    -------
    WorkspaceGraph
        The graph the CLI loads instead of reading the filesystem.
    """
    crate_root = root / "crate"
    crate = WorkspaceCrate(
        id="crate-id",
        name="crate",
        version="0.1.0",
        manifest_path=crate_root / "Cargo.toml",
        root_path=crate_root,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )
    return WorkspaceGraph(workspace_root=root, crates=(crate,))


def test_main_installs_the_termination_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bootstrap must install the handler, not merely define it.

    The end-to-end tests install it themselves inside their probe, so they
    prove the handler works without proving anything reaches it. A handler
    nothing installs leaves every staged copy behind on termination, which is
    the defect in issue #269 restored in full.
    """
    installed: list[bool] = []
    monkeypatch.setattr(
        cli.publish_staging,
        "install_termination_cleanup",
        lambda: installed.append(True),
    )
    monkeypatch.setattr(cli, "load_workspace", lambda _: _make_workspace(tmp_path))
    monkeypatch.setattr(publish_command, "run", lambda *_a, **_k: "done")

    exit_code = cli.main(["publish", "--workspace-root", str(tmp_path)])

    assert exit_code == 0
    assert installed == [True], "lading.cli.main did not install the handler"


class _KeepStagingCase(typ.NamedTuple):
    """One way of asking for, or declining, a retained staging copy.

    Attributes
    ----------
    environment : str | None
        Value for ``LADING_KEEP_STAGING``, or ``None`` to leave it unset.
    argument : str | None
        Extra command-line argument, or ``None`` for none.
    expected_cleanup : bool
        The ``PublishOptions.cleanup`` the CLI should compose.
    """

    environment: str | None
    argument: str | None
    expected_cleanup: bool


@pytest.mark.parametrize(
    "case",
    [
        _KeepStagingCase(None, None, expected_cleanup=True),
        _KeepStagingCase(None, "--keep-staging", expected_cleanup=False),
        _KeepStagingCase("1", None, expected_cleanup=False),
        _KeepStagingCase("0", None, expected_cleanup=True),
        _KeepStagingCase("1", "--no-keep-staging", expected_cleanup=True),
    ],
    ids=["default", "flag", "env-set", "env-unset", "flag-beats-env"],
)
def test_keep_staging_resolves_to_the_cleanup_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _KeepStagingCase,
) -> None:
    """``--keep-staging`` and its variable must reach ``PublishOptions``.

    The flag is the negative of the option it sets, so a wiring mistake is
    invisible: the command still runs, and the only evidence is a staged copy
    that survives, or one that vanishes while someone is debugging it. The
    environment cases matter because Cyclopts resolves the variable, and the
    command line has to win over it.
    """
    captured: list[publish_command.PublishOptions] = []

    def fake_run(*arguments: object, options: publish_command.PublishOptions) -> str:
        """Record the options the CLI composed."""
        del arguments
        captured.append(options)
        return "done"

    monkeypatch.setattr(publish_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: _make_workspace(tmp_path))
    if case.environment is None:
        monkeypatch.delenv(cli_options.KEEP_STAGING_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(cli_options.KEEP_STAGING_ENV_VAR, case.environment)

    arguments = ["publish", "--workspace-root", str(tmp_path)]
    if case.argument is not None:
        arguments.append(case.argument)

    assert cli.main(arguments) == 0
    assert captured[-1].cleanup is case.expected_cleanup
