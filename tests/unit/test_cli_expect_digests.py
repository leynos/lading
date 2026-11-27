"""Unit tests for the ``lading publish --expect-digests`` option."""

import collections.abc as cabc
import logging
import typing as typ
from pathlib import Path

import pytest

from lading import cli, cli_environment
from lading.cli_options import PublishFlags
from lading.commands import publish as publish_command

if typ.TYPE_CHECKING:
    from lading.commands.publish_cargo_config import CargoEnvironment
    from lading.workspace import WorkspaceCrate, WorkspaceGraph


@pytest.fixture
def captured_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_workspace: cabc.Callable[..., WorkspaceGraph],
    make_crate: cabc.Callable[..., WorkspaceCrate],
) -> dict[str, publish_command.PublishOptions]:
    """Stub the publish command and collect the options the CLI hands it."""
    monkeypatch.delenv(cli_environment.CARGO_REGISTRY_DEFAULT_ENV_VAR, raising=False)
    root = tmp_path.resolve()
    graph = make_workspace(root, make_crate(root, "crate"))
    captured: dict[str, publish_command.PublishOptions] = {}

    def fake_run(
        workspace_root: Path,
        configuration: object,
        workspace_model: object,
        *,
        options: publish_command.PublishOptions | None = None,
    ) -> str:
        """Capture the publish options and return a canned summary."""
        del workspace_root, configuration, workspace_model
        assert options is not None, "the CLI must pass PublishOptions"
        captured["options"] = options
        return "publish"

    monkeypatch.setattr(publish_command, "run", fake_run)
    monkeypatch.setattr(cli, "load_workspace", lambda _, **__: graph)
    return captured


def _publish(tmp_path: Path, *extra_args: str) -> int:
    """Run ``lading publish`` through the CLI with ``extra_args``."""
    return cli.main(["--workspace-root", str(tmp_path), "publish", *extra_args])


def _environment(
    captured: dict[str, publish_command.PublishOptions],
) -> CargoEnvironment:
    """Return the Cargo environment the CLI resolved, which must be present."""
    resolved = captured["options"].cargo_environment
    assert resolved is not None, "digests require a resolved Cargo environment"
    return resolved


def test_expect_digests_path_reaches_the_publish_options(
    tmp_path: Path, captured_options: dict[str, publish_command.PublishOptions]
) -> None:
    """``--expect-digests PATH`` is forwarded as a path, unresolved."""
    assert _publish(tmp_path, "--expect-digests", "digests.txt") == 0, (
        "publish must succeed with the stubbed run"
    )

    assert captured_options["options"].expect_digests == Path("digests.txt"), (
        "the path must reach the options unresolved"
    )


def test_option_is_absent_by_default(
    tmp_path: Path, captured_options: dict[str, publish_command.PublishOptions]
) -> None:
    """Without the option, publish receives no expectation at all."""
    assert _publish(tmp_path) == 0, "publish must succeed with the stubbed run"

    assert captured_options["options"].expect_digests is None, (
        "no expectation may be set by default"
    )


def test_default_registry_is_read_from_the_cargo_variable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    captured_options: dict[str, publish_command.PublishOptions],
) -> None:
    """The composition root passes ``CARGO_REGISTRY_DEFAULT`` through."""
    monkeypatch.setenv(cli_environment.CARGO_REGISTRY_DEFAULT_ENV_VAR, "internal")

    assert _publish(tmp_path, "--expect-digests", "digests.txt") == 0, (
        "publish must succeed with the stubbed run"
    )

    assert _environment(captured_options).default_registry == "internal", (
        "the default registry must come from the environment"
    )


def test_option_appears_in_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    """The option is documented in ``lading publish --help``."""
    assert cli.main(["publish", "--help"]) == 0, "help must succeed"

    assert "--expect-digests" in capsys.readouterr().out, (
        "the option must be documented in help"
    )


def _stale_lockfile_runner(
    commands: list[tuple[str, ...]],
) -> cabc.Callable[..., tuple[int, str, str]]:
    """Return a runner whose ``cargo metadata`` fails like a stale lockfile."""

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record the command; refuse to update the lock when ``--locked``."""
        del cwd, env, echo_stdout
        commands.append(tuple(command))
        if "--locked" in command:
            return (
                101,
                "",
                "error: the lock file needs to be updated but --locked was passed",
            )
        return 101, "", "error: unrelated metadata failure"

    return runner


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((("--expect-digests", "digests.txt"), True), id="with_digests"),
        pytest.param(((), False), id="without_digests"),
    ],
)
def test_discovery_is_locked_only_when_digests_are_expected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[tuple[str, ...], bool],
) -> None:
    """A stale lockfile stops discovery instead of being updated first."""
    extra_args, is_locked = case
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        cli, "_select_runner", lambda *_a, **_k: _stale_lockfile_runner(commands)
    )

    assert _publish(tmp_path, *extra_args) == 1, "discovery must fail in both cases"

    assert commands, "workspace discovery must have run cargo metadata"
    assert all(command[:2] == ("cargo", "metadata") for command in commands), (
        "only workspace discovery may run before the failure"
    )
    assert all(("--locked" in command) is is_locked for command in commands), (
        "--locked must be passed to discovery exactly when digests are expected"
    )
    if is_locked:
        assert "--locked was passed" in capsys.readouterr().err, (
            "the stale-lockfile failure must reach the operator"
        )


def test_cargo_home_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    captured_options: dict[str, publish_command.PublishOptions],
) -> None:
    """The composition root resolves Cargo home from ``CARGO_HOME``."""
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "cargo-home"))

    assert _publish(tmp_path, "--expect-digests", "digests.txt") == 0, (
        "publish must succeed with the stubbed run"
    )

    assert _environment(captured_options).cargo_home == tmp_path / "cargo-home", (
        "CARGO_HOME must name Cargo's home"
    )


def test_cargo_home_defaults_to_the_dot_cargo_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    captured_options: dict[str, publish_command.PublishOptions],
) -> None:
    """Without ``CARGO_HOME``, Cargo's home is ``~/.cargo``."""
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert _publish(tmp_path, "--expect-digests", "digests.txt") == 0, (
        "publish must succeed with the stubbed run"
    )

    assert _environment(captured_options).cargo_home == tmp_path / "home" / ".cargo", (
        "the default Cargo home is ~/.cargo"
    )


def test_runner_without_digests_is_the_plain_one_even_with_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 8: no option, no redaction, whatever the environment holds."""
    monkeypatch.delenv("LADING_USE_CMD_MOX_STUB", raising=False)
    monkeypatch.setenv("CARGO_REGISTRY_TOKEN", "some-token-value")

    assert cli._select_runner() is cli.subprocess_runner, (
        "the plain runner must be used exactly as before the option existed"
    )


def _unreadable_credentials_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, leak: str
) -> None:
    """Point Cargo home at a path holding ``leak``, with an unreadable file."""
    home = tmp_path / f"cargo-{leak}"
    home.mkdir()
    (home / "credentials.toml").write_text("[registry\n", encoding="utf-8")
    monkeypatch.setenv("CARGO_HOME", str(home))
    monkeypatch.setenv("CARGO_REGISTRY_TOKEN", leak)


def _assert_warning_hides(caplog: pytest.LogCaptureFixture, leak: str) -> None:
    """Assert the credentials-file warning was logged without ``leak``."""
    assert "Ignoring unreadable Cargo credentials file" in caplog.text, (
        "the warning must still be reported"
    )
    assert leak not in caplog.text, "the token in the path must be hidden"


def test_credentials_file_warning_is_redacted_before_its_tokens_are_known(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    captured_options: dict[str, publish_command.PublishOptions],
) -> None:
    """A token in the Cargo home path is hidden through ``cli.main``."""
    _unreadable_credentials_home(monkeypatch, tmp_path, "home-credential-12ab34cd56ef")
    caplog.set_level(logging.WARNING)

    assert _publish(tmp_path, "--expect-digests", "digests.txt") == 0, (
        "publish must succeed with the stubbed run"
    )

    _assert_warning_hides(caplog, "home-credential-12ab34cd56ef")
    assert captured_options, "the stubbed publish must have run"


def test_direct_publish_hides_the_token_before_reading_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    captured_options: dict[str, publish_command.PublishOptions],
) -> None:
    """Calling ``cli.publish`` directly redacts the credentials-file warning too."""
    _unreadable_credentials_home(
        monkeypatch, tmp_path, "direct-credential-98fe76dc54ba"
    )
    caplog.set_level(logging.WARNING)

    cli.publish(tmp_path, flags=PublishFlags(expect_digests=Path("digests.txt")))

    _assert_warning_hides(caplog, "direct-credential-98fe76dc54ba")
    assert captured_options, "the stubbed publish must have run"
