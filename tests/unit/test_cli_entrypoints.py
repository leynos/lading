"""Unit tests for the ``lading.cli`` end-to-end entry points.

Covers behaviour through the public application boundary rather than white-box
calls: exception translation, ``bump`/`publish` argument validation, the
workspace environment scope, and the two ``_run_with_context`` configuration
branches.
"""

import collections.abc as cabc
import dataclasses as dc
import os
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading import config as config_module
from lading.commands import bump as bump_command
from lading.commands import publish as publish_command
from tests.helpers.cli_workspace import make_workspace

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceGraph


@dc.dataclass(frozen=True, slots=True)
class ExceptionHandlingCase:
    """Test case for exception handling validation."""

    exception: BaseException
    expected_exit_code: int
    expected_message: str


@pytest.mark.parametrize(
    "case",
    [
        ExceptionHandlingCase(
            exception=KeyboardInterrupt(),
            expected_exit_code=130,
            expected_message="Operation cancelled",
        ),
        ExceptionHandlingCase(
            exception=RuntimeError("boom"),
            expected_exit_code=1,
            expected_message="Unexpected error",
        ),
    ],
)
@pytest.mark.usefixtures("minimal_config")
def test_main_handles_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    case: ExceptionHandlingCase,
) -> None:
    """Handle exceptions during command execution."""

    def boom(_: cabc.Sequence[str]) -> int:
        raise case.exception

    monkeypatch.setattr(cli, "_dispatch_and_print", boom)
    exit_code = cli.main(["bump", "1.2.3", "--workspace-root", str(tmp_path)])
    assert exit_code == case.expected_exit_code, (
        f"expected exit code {case.expected_exit_code}, got {exit_code}"
    )
    captured = capsys.readouterr()
    assert case.expected_message in captured.err, (
        f"stderr should report {case.expected_message!r}"
    )


@pytest.mark.usefixtures("minimal_config")
def test_bump_command_validates_version(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject bump invocations that provide an invalid version string."""

    def fail(*_: object, **__: object) -> typ.NoReturn:
        pytest.fail("bump.run should not be invoked for invalid versions")

    monkeypatch.setattr(bump_command, "run", fail)
    exit_code = cli.main(["bump", "1.2", "--workspace-root", str(tmp_path)])
    assert exit_code == 1, f"an invalid version must exit 1, got {exit_code}"
    captured = capsys.readouterr()
    # Cyclopts renders argument-validation errors through its own console
    # (stdout), consistent with other cyclopts errors such as "Unknown command".
    assert "Invalid version argument '1.2'" in captured.out, (
        "cyclopts should report the invalid version on stdout"
    )


@pytest.mark.usefixtures("minimal_config")
def test_bump_command_accepts_extended_semver(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Accept semantic versions with pre-release and build metadata."""
    graph = make_workspace(tmp_path.resolve())
    monkeypatch.setattr(cli, "load_workspace", lambda _: graph)
    captured: dict[str, object] = {}

    def fake_run(
        workspace_root: Path,
        version: str,
        *,
        options: bump_command.BumpOptions,
    ) -> str:
        captured["workspace_root"] = workspace_root
        captured["version"] = version
        captured["options"] = options
        return "ok"

    monkeypatch.setattr(bump_command, "run", fake_run)
    version = "1.2.3-alpha.1+build.5"
    exit_code = cli.main(["bump", version, "--workspace-root", str(tmp_path)])
    assert exit_code == 0, f"a valid version must exit 0, got {exit_code}"
    capsys.readouterr()
    assert captured["workspace_root"] == tmp_path.resolve(), (
        "bump.run must receive the resolved workspace root"
    )
    assert captured["version"] == version, "bump.run must receive the given version"
    options = captured["options"]
    assert isinstance(options, bump_command.BumpOptions), (
        "bump.run must receive a BumpOptions instance"
    )
    assert isinstance(options.configuration, config_module.LadingConfig), (
        "the options must carry the loaded LadingConfig"
    )
    assert options.workspace is graph, "the options must carry the injected workspace"
    assert options.dry_run is False, "a plain invocation must not be a dry run"


@pytest.mark.usefixtures("minimal_config")
def test_cyclopts_invoke_uses_workspace_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invoke the Cyclopts app directly with workspace override propagation."""
    graph = make_workspace(tmp_path.resolve())
    monkeypatch.setattr(cli, "load_workspace", lambda _: graph)

    def fake_run(
        workspace_root: Path,
        version: str,
        *,
        options: bump_command.BumpOptions,
    ) -> str:
        assert workspace_root == tmp_path.resolve(), (
            "the workspace override should reach bump.run"
        )
        assert version == "4.5.6", "bump.run should receive the parsed version"
        assert isinstance(options.configuration, config_module.LadingConfig), (
            "the options must carry the loaded LadingConfig"
        )
        assert options.workspace is graph, (
            "the options must carry the injected workspace"
        )
        assert options.dry_run is False, "a plain invocation must not be a dry run"
        return "bump summary"

    monkeypatch.setattr(bump_command, "run", fake_run)
    result = cli.app(["bump", "4.5.6", "--workspace-root", str(tmp_path)])
    assert result == "bump summary", "cli.app should return the command result"


def test_workspace_env_sets_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure the workspace variable only exists while the context is active."""
    monkeypatch.delenv(cli.WORKSPACE_ROOT_ENV_VAR, raising=False)
    with cli._workspace_env(tmp_path):
        assert os.environ[cli.WORKSPACE_ROOT_ENV_VAR] == str(tmp_path), (
            "the workspace root must be exported while the context is active"
        )
    assert cli.WORKSPACE_ROOT_ENV_VAR not in os.environ, (
        "the workspace variable must be removed once the context exits"
    )


def test_run_with_context_branches_behave_identically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    write_config: cabc.Callable[[str], Path],
) -> None:
    """Pre-loaded and freshly-loaded configuration take the same path.

    Issue #107 (13c): `_run_with_context` previously duplicated the
    load-workspace-and-run block across both branches; this pins identical
    downstream behaviour for each.
    """
    write_config("")
    workspace_graph = make_workspace(tmp_path.resolve())
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    calls: list[tuple[object, ...]] = []

    def runner(
        root: Path,
        configuration: config_module.LadingConfig,
        workspace: WorkspaceGraph,
        command_runner: object,
    ) -> str:
        calls.append((root, configuration, workspace, command_runner))
        return "ran"

    fresh_result = cli._run_with_context(tmp_path.resolve(), runner)

    configuration = config_module.load_configuration(tmp_path)
    with config_module.use_configuration(configuration):
        preloaded_result = cli._run_with_context(tmp_path.resolve(), runner)

    assert fresh_result == preloaded_result == "ran", (
        "both branches must return the runner's result"
    )
    assert len(calls) == 2, "the runner should execute exactly once per branch"
    fresh_call, preloaded_call = calls
    assert fresh_call[0] == preloaded_call[0] == tmp_path.resolve(), (
        "both branches must pass the resolved workspace root"
    )
    assert isinstance(fresh_call[1], config_module.LadingConfig), (
        "the fresh branch must load a LadingConfig from disk"
    )
    assert preloaded_call[1] is configuration, (
        "the preloaded branch must reuse the active configuration object"
    )
    assert fresh_call[2] is workspace_graph, (
        "the fresh branch must pass the loaded workspace graph"
    )
    assert preloaded_call[2] is workspace_graph, (
        "the preloaded branch must pass the loaded workspace graph"
    )


def test_publish_via_app_matches_across_config_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    minimal_config: Path,
) -> None:
    """Public ``lading publish`` behaves the same for both config branches.

    Issue #107 (13c): this exercises both ``_run_with_context`` branches
    through the public Cyclopts command boundary (``cli.app``), complementing
    the white-box ``test_run_with_context_branches_behave_identically``.
    ``cli.main`` always installs configuration before dispatch, so the
    disk-loaded branch is only reachable publicly via ``cli.app`` invoked
    without an active configuration scope.
    """
    assert minimal_config.exists(), (
        "the minimal_config fixture must write lading.toml for the disk-loaded path"
    )
    workspace_graph = make_workspace(tmp_path.resolve())
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    calls: list[tuple[Path, config_module.LadingConfig, WorkspaceGraph]] = []

    def fake_run(
        root: Path,
        configuration: config_module.LadingConfig,
        workspace: WorkspaceGraph,
        *,
        options: publish_command.PublishOptions,
    ) -> str:
        assert isinstance(options, publish_command.PublishOptions), (
            "publish.run must receive a PublishOptions instance"
        )
        calls.append((root, configuration, workspace))
        return "published"

    monkeypatch.setattr(publish_command, "run", fake_run)
    args = ["publish", "--workspace-root", str(tmp_path)]

    # Disk-loaded branch: no configuration is active, so `_run_with_context`
    # loads `lading.toml` from disk.
    disk_result = cli.app(args)

    # Pre-loaded branch: an active configuration scope makes the same call
    # reuse that configuration under a nullcontext instead of reloading.
    preloaded = config_module.load_configuration(tmp_path)
    with config_module.use_configuration(preloaded):
        preloaded_result = cli.app(args)

    assert disk_result == preloaded_result == "published", (
        "both config branches must return the same command result"
    )
    assert len(calls) == 2, "publish.run should execute exactly once per branch"
    disk_call, preloaded_call = calls
    # Identical downstream behaviour: same workspace root, same injected
    # workspace graph, and equal configuration content for both branches.
    assert disk_call[0] == preloaded_call[0] == tmp_path.resolve(), (
        "both branches must pass the resolved workspace root"
    )
    assert disk_call[2] is preloaded_call[2] is workspace_graph, (
        "both branches must pass the injected workspace graph"
    )
    assert disk_call[1] == preloaded_call[1], (
        "both branches must resolve equal configuration content"
    )
    # The branches differ only in provenance: the disk branch reloads a fresh
    # config object; the pre-loaded branch reuses the active one.
    assert preloaded_call[1] is preloaded, (
        "the preloaded branch must reuse the active configuration object"
    )
    assert disk_call[1] is not preloaded, (
        "the disk branch must reload a fresh configuration object"
    )
