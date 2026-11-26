"""Unit tests for the ``lading publish --expect-digests`` option."""

import collections.abc as cabc
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading.commands import publish as publish_command

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceCrate, WorkspaceGraph


@pytest.fixture
def captured_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_workspace: cabc.Callable[..., WorkspaceGraph],
    make_crate: cabc.Callable[..., WorkspaceCrate],
) -> dict[str, publish_command.PublishOptions]:
    """Stub the publish command and collect the options the CLI hands it."""
    monkeypatch.delenv(cli.CARGO_REGISTRY_DEFAULT_ENV_VAR, raising=False)
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
    monkeypatch.setenv(cli.CARGO_REGISTRY_DEFAULT_ENV_VAR, "internal")

    assert _publish(tmp_path, "--expect-digests", "digests.txt") == 0, (
        "publish must succeed with the stubbed run"
    )

    assert captured_options["options"].default_registry == "internal", (
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
    monkeypatch.setattr(cli, "_select_runner", lambda: _stale_lockfile_runner(commands))

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
