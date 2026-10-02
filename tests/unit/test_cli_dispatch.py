"""Unit tests for ``lading.cli`` subcommand dispatch.

Covers ``cli.main`` selecting a command, rejecting a missing or invalid
subcommand, and translating a rejected ``--log-level`` into an exit code.
"""

import logging
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading import config as config_module
from lading.commands import bump as bump_command
from lading.commands import publish as publish_command
from tests.helpers.cli_workspace import make_workspace, preserve_root_logger
from tests.helpers.cwd import chdir_for_test
from tests.unit.test_cli_facade import CommandDispatchCase


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

    workspace_graph = make_workspace(tmp_path.resolve())

    def fake_run(*args: object, **kwargs: object) -> str:
        called["args"] = args
        called["kwargs"] = kwargs
        return case.return_value

    monkeypatch.setattr(case.command_module, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    args = [arg.replace("{tmp_path}", str(tmp_path)) for arg in case.cli_args]
    assert case.command_name in args, "the args must name the subcommand under test"
    exit_code = cli.main(args)
    assert exit_code == 0, "the dispatched command must exit cleanly"
    captured_args = called["args"]
    captured_kwargs = called["kwargs"]
    if case.command_module is bump_command:
        workspace_root_arg, version_arg = captured_args
        assert workspace_root_arg == tmp_path.resolve(), (
            "bump must receive the resolved workspace root"
        )
        assert version_arg == case.expected_version, (
            "bump must receive the requested version"
        )
        options = captured_kwargs["options"]
        assert isinstance(options, bump_command.BumpOptions), (
            "the dispatched options must be BumpOptions"
        )
        assert isinstance(options.configuration, config_module.LadingConfig), (
            "the bump configuration must be a LadingConfig"
        )
        workspace_model = options.workspace
        assert options.dry_run is False, "a plain bump must not be a dry run"
    else:
        workspace_root_arg, configuration, workspace_model = captured_args
        assert workspace_root_arg == tmp_path.resolve(), (
            "publish must receive the resolved workspace root"
        )
        assert configuration.publish.strip_patches == "all", (
            "publish must receive the loaded configuration"
        )
    assert workspace_model is workspace_graph, (
        "the command must receive the loaded workspace"
    )
    captured = capsys.readouterr()
    assert case.return_value in captured.out, (
        "the command summary must be printed to stdout"
    )


def test_main_handles_missing_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return an error when no subcommand is provided."""
    exit_code = cli.main([])
    assert exit_code == 2, "a missing subcommand must exit with code 2"
    captured = capsys.readouterr()
    assert "Usage" in captured.out, "the usage message must be printed"


@pytest.mark.usefixtures("minimal_config")
def test_main_handles_invalid_subcommand(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Report an error when the subcommand is unknown."""
    chdir_for_test(monkeypatch, tmp_path)
    exit_code = cli.main(["invalid"])
    assert exit_code != 0, "an unknown subcommand must fail"
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out, (
        "the unknown-command error must be printed"
    )


@pytest.mark.usefixtures("minimal_config")
@pytest.mark.parametrize(
    ("env_value", "sentinel_state"),
    [
        pytest.param(None, "present", id="default-info"),
        pytest.param("INFO", "present", id="explicit-info"),
        pytest.param("WARNING", "absent", id="suppress-info"),
    ],
)
def test_main_emits_publish_command_logs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_value: str | None,
    sentinel_state: typ.Literal["present", "absent"],
) -> None:
    """Ensure publish logging honours ``LADING_LOG_LEVEL``."""
    workspace_graph = make_workspace(tmp_path.resolve())

    def fake_run(
        workspace_root: Path,
        configuration: object,
        workspace_model: object,
        *,
        options: object | None = None,
    ) -> str:
        logging.getLogger("lading.commands.publish").info("Sentinel command log")
        logging.getLogger("lading.commands.publish").warning(
            "Elevated sentinel command log"
        )
        return "done"

    monkeypatch.setattr(publish_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    sentinel = "Sentinel command log"
    elevated = "Elevated sentinel command log"

    with preserve_root_logger():
        if env_value is None:
            monkeypatch.delenv(cli.LOG_LEVEL_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(cli.LOG_LEVEL_ENV_VAR, env_value)

        exit_code = cli.main(["publish", "--workspace-root", str(tmp_path)])
        assert exit_code == 0, "the publish invocation must exit cleanly"
        captured = capsys.readouterr()

    if sentinel_state == "present":
        assert sentinel in captured.err, "the info log must be emitted at INFO level"
    else:
        assert sentinel not in captured.err, "the info log must stay hidden below INFO"
    assert elevated in captured.err, "the warning log must always be emitted"


def test_main_uses_defaults_when_configuration_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI commands fall back to default configuration when no file exists."""
    workspace_graph = make_workspace(tmp_path.resolve())
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)
    captured: dict[str, typ.Any] = {}

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
    exit_code = cli.main(["bump", "1.2.3", "--workspace-root", str(tmp_path)])

    assert exit_code == 0, "the bump invocation must exit cleanly"
    assert captured["workspace_root"] == tmp_path.resolve(), (
        "bump must receive the resolved workspace root"
    )
    assert captured["version"] == "1.2.3", "bump must receive the requested version"
    options = typ.cast("bump_command.BumpOptions", captured["options"])
    assert options.configuration == config_module.LadingConfig(), (
        "a missing configuration must fall back to defaults"
    )


@pytest.mark.usefixtures("minimal_config")
def test_main_rejects_invalid_log_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invalid ``LADING_LOG_LEVEL`` should abort early with a clear message."""
    workspace_graph = make_workspace(tmp_path.resolve())
    monkeypatch.setenv(cli.LOG_LEVEL_ENV_VAR, "not-a-level")
    monkeypatch.setattr(cli, "load_workspace", lambda _: workspace_graph)

    with preserve_root_logger(), pytest.raises(SystemExit) as excinfo:
        cli.main(["publish", "--workspace-root", str(tmp_path)])

    assert cli.LOG_LEVEL_ENV_VAR in str(excinfo.value), (
        "the error must name the log-level environment variable"
    )
