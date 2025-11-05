"""Unit tests for the lading CLI scaffolding."""

from __future__ import annotations

import dataclasses as dc
import logging
import os
import typing as typ

import pytest

from lading import cli
from lading import config as config_module
from lading.commands import bump as bump_command
from lading.commands import publish as publish_command
from lading.utils import normalise_workspace_root
from lading.workspace import WorkspaceCrate, WorkspaceGraph

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


@dc.dataclass(frozen=True)
class CommandDispatchCase:
    """Test case for command dispatch validation."""

    command_module: ModuleType
    command_name: str
    return_value: str
    cli_args: list[str]
    expected_version: str | None = None


@dc.dataclass(frozen=True)
class ExceptionHandlingCase:
    """Test case for exception handling validation."""

    exception: BaseException
    expected_exit_code: int
    expected_message: str


@pytest.mark.parametrize(
    ("tokens", "expected_workspace", "expected_remaining"),
    [
        ([], None, []),
        (["bump"], None, ["bump"]),
        (["--workspace-root", "workspace", "publish"], "workspace", ["publish"]),
        (["--workspace-root=workspace", "bump"], "workspace", ["bump"]),
        (["bump", "--workspace-root", "workspace"], "workspace", ["bump"]),
        (
            [
                "--workspace-root=first",
                "--workspace-root",
                "second",
                "publish",
            ],
            "second",
            ["publish"],
        ),
    ],
)
def test_extract_workspace_override(
    tokens: typ.Sequence[str],
    expected_workspace: str | None,
    expected_remaining: list[str],
) -> None:
    """Extract workspace overrides from CLI tokens."""
    workspace, remaining = cli._extract_workspace_override(tokens)
    assert workspace == expected_workspace
    assert remaining == expected_remaining


def test_extract_workspace_override_requires_value() -> None:
    """Require a value whenever ``--workspace-root`` appears."""
    with pytest.raises(SystemExit):
        cli._extract_workspace_override(["--workspace-root"])


def test_extract_workspace_override_requires_value_equals() -> None:
    """Reject ``--workspace-root=`` when no value is supplied."""
    with pytest.raises(SystemExit):
        cli._extract_workspace_override(["--workspace-root="])


def test_normalise_workspace_root_defaults_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Default workspace resolution uses the current working directory."""
    monkeypatch.chdir(tmp_path)
    resolved = normalise_workspace_root(None)
    assert resolved == tmp_path.resolve()


def _make_workspace(root: Path) -> WorkspaceGraph:
    """Return a representative workspace graph for CLI tests."""
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


@pytest.mark.usefixtures("minimal_config")
@pytest.mark.parametrize(
    "case",
    [
        CommandDispatchCase(
            command_module=bump_command,
            command_name="bump",
            return_value="bump summary",
            cli_args=["--workspace-root", "{tmp_path}", "bump", "7.8.9"],
            expected_version="7.8.9",
        ),
        CommandDispatchCase(
            command_module=publish_command,
            command_name="publish",
            return_value="publish placeholder",
            cli_args=["publish", "--workspace-root", "{tmp_path}"],
        ),
    ],
)
def test_main_dispatches_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: CommandDispatchCase,
) -> None:
    """Route subcommands through their placeholder implementations."""
    called: dict[str, typ.Any] = {}

    workspace_graph = _make_workspace(tmp_path.resolve())

    def fake_run(*args: object, **kwargs: object) -> str:
        called["args"] = args
        called["kwargs"] = kwargs
        return case.return_value

    monkeypatch.setattr(case.command_module, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    args = [arg.replace("{tmp_path}", str(tmp_path)) for arg in case.cli_args]
    assert case.command_name in args
    exit_code = cli.main(args)
    assert exit_code == 0
    captured_args = called["args"]
    captured_kwargs = called["kwargs"]
    if case.command_module is bump_command:
        workspace_root_arg, version_arg = captured_args
        assert workspace_root_arg == tmp_path.resolve()
        assert version_arg == case.expected_version
        options = captured_kwargs["options"]
        assert isinstance(options, bump_command.BumpOptions)
        assert isinstance(options.configuration, config_module.LadingConfig)
        workspace_model = options.workspace
        assert options.dry_run is False
    else:
        workspace_root_arg, configuration, workspace_model = captured_args
        assert workspace_root_arg == tmp_path.resolve()
        assert configuration.publish.strip_patches == "all"
    assert workspace_model is workspace_graph
    captured = capsys.readouterr()
    assert case.return_value in captured.out


def test_main_handles_missing_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return an error when no subcommand is provided."""
    exit_code = cli.main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Usage" in captured.out


@pytest.mark.usefixtures("minimal_config")
def test_main_handles_invalid_subcommand(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Report an error when the subcommand is unknown."""
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["invalid"])
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out


@pytest.mark.usefixtures("minimal_config")
def test_main_emits_publish_command_logs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ensure publish command logging is wired to stderr by default."""
    workspace_graph = _make_workspace(tmp_path.resolve())
    lading_logger = logging.getLogger("lading")
    prior_handlers = list(lading_logger.handlers)
    prior_level = lading_logger.level
    prior_propagation = lading_logger.propagate

    def fake_run(
        workspace_root: Path,
        configuration: object,
        workspace_model: object,
        *,
        options: object | None = None,
    ) -> str:
        logging.getLogger("lading.commands.publish").info("Sentinel command log")
        return "done"

    monkeypatch.setattr(publish_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    try:
        exit_code = cli.main(["publish", "--workspace-root", str(tmp_path)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Sentinel command log" in captured.err
    finally:
        for handler in list(lading_logger.handlers):
            lading_logger.removeHandler(handler)
        for handler in prior_handlers:
            lading_logger.addHandler(handler)
        lading_logger.setLevel(prior_level)
        lading_logger.propagate = prior_propagation


def test_main_reports_missing_configuration(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Return a clear error when configuration is missing."""
    exit_code = cli.main(["bump", "1.2.3", "--workspace-root", str(tmp_path)])
    assert exit_code == 1
    captured = capsys.readouterr()
    expected_path = tmp_path.resolve() / config_module.CONFIG_FILENAME
    assert (
        f"Configuration error: Configuration file not found: {expected_path}"
        in captured.err
    )


@pytest.mark.usefixtures("minimal_config")
def test_bump_cli_accepts_dry_run_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CLI passes ``dry_run=True`` when the flag is provided."""
    workspace_graph = _make_workspace(tmp_path.resolve())
    captured_kwargs: dict[str, typ.Any] = {}

    def fake_run(*args: object, **kwargs: object) -> str:
        captured_kwargs.update(kwargs)
        return "preview"

    monkeypatch.setattr(bump_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)

    exit_code = cli.main(
        [
            "--workspace-root",
            str(tmp_path),
            "bump",
            "1.2.3",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    options = captured_kwargs["options"]
    assert isinstance(options, bump_command.BumpOptions)
    assert options.dry_run is True


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

    def boom(_: typ.Sequence[str]) -> int:
        raise case.exception

    monkeypatch.setattr(cli, "_dispatch_and_print", boom)
    exit_code = cli.main(["bump", "1.2.3", "--workspace-root", str(tmp_path)])
    assert exit_code == case.expected_exit_code
    captured = capsys.readouterr()
    assert case.expected_message in captured.err


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
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Invalid version argument '1.2'" in captured.err


@pytest.mark.usefixtures("minimal_config")
def test_bump_command_accepts_extended_semver(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Accept semantic versions with pre-release and build metadata."""
    graph = _make_workspace(tmp_path.resolve())
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
    assert exit_code == 0
    capsys.readouterr()
    assert captured["workspace_root"] == tmp_path.resolve()
    assert captured["version"] == version
    options = captured["options"]
    assert isinstance(options, bump_command.BumpOptions)
    assert isinstance(options.configuration, config_module.LadingConfig)
    assert options.workspace is graph
    assert options.dry_run is False


@pytest.mark.usefixtures("minimal_config")
def test_cyclopts_invoke_uses_workspace_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invoke the Cyclopts app directly with workspace override propagation."""
    graph = _make_workspace(tmp_path.resolve())
    monkeypatch.setattr(cli, "load_workspace", lambda _: graph)

    def fake_run(
        workspace_root: Path,
        version: str,
        *,
        options: bump_command.BumpOptions,
    ) -> str:
        assert workspace_root == tmp_path.resolve()
        assert version == "4.5.6"
        assert isinstance(options.configuration, config_module.LadingConfig)
        assert options.workspace is graph
        assert options.dry_run is False
        return "bump summary"

    monkeypatch.setattr(bump_command, "run", fake_run)
    result = cli.app(["bump", "4.5.6", "--workspace-root", str(tmp_path)])
    assert result == "bump summary"


def test_workspace_env_sets_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure the workspace variable only exists while the context is active."""
    monkeypatch.delenv(cli.WORKSPACE_ROOT_ENV_VAR, raising=False)
    with cli._workspace_env(tmp_path):
        assert os.environ[cli.WORKSPACE_ROOT_ENV_VAR] == str(tmp_path)
    assert cli.WORKSPACE_ROOT_ENV_VAR not in os.environ
