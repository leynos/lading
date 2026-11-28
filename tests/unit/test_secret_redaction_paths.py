"""Credential paths and logging parity for the redacting runner."""

import logging
import sys
import typing as typ
from pathlib import Path

from lading import cli, cli_environment
from lading.commands import publish, publish_digest_run, publish_pipeline
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_preflight import _AuxBuildContext
from lading.runtime import subprocess_runner
from lading.runtime.credentials import credential_file_tokens
from lading.runtime.redacting_runner import redacting_runner
from lading.utils.redaction import REDACTED
from lading.utils.secret_scope import SecretScope

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import pytest

_FROM_FILE = "file-credential-9f8e7d6c5b4a"
_FROM_NAMED = "named-credential-1a2b3c4d5e6f"
_FROM_OVERRIDE = "override-credential-0a1b2c3d4e5f"


def _echoing(value: str) -> list[str]:
    """Return a command that prints ``value`` to stdout."""
    return [sys.executable, "-c", f"print({value!r})"]


def _write_credentials(home: Path, name: str, text: str) -> None:
    """Write a Cargo credentials file under ``home``."""
    home.mkdir(parents=True, exist_ok=True)
    (home / name).write_text(text, encoding="utf-8")


def test_credentials_file_tokens_are_found_in_both_names(tmp_path: Path) -> None:
    """``registry.token`` and ``registries.<name>.token`` are collected."""
    _write_credentials(
        tmp_path, "credentials.toml", f'[registry]\ntoken = "{_FROM_FILE}"\n'
    )
    _write_credentials(
        tmp_path, "credentials", f'[registries.internal]\ntoken = "{_FROM_NAMED}"\n'
    )

    assert credential_file_tokens(tmp_path) == tuple(
        sorted((_FROM_FILE, _FROM_NAMED), key=lambda value: (-len(value), value))
    ), "tokens from both file names must be collected"


def test_unreadable_credentials_file_warns_without_its_contents(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed file adds nothing and its contents never reach the log."""
    caplog.set_level(logging.WARNING)
    _write_credentials(tmp_path, "credentials.toml", f'token = "{_FROM_FILE}\n[oops')

    assert not credential_file_tokens(tmp_path), "a malformed file contributes none"
    assert "Ignoring unreadable Cargo credentials file" in caplog.text, (
        "the failure must be reported"
    )
    assert _FROM_FILE not in caplog.text, "the warning must not quote the file"


def test_missing_home_or_file_contributes_nothing(tmp_path: Path) -> None:
    """No Cargo home, or no credentials file, is simply no values."""
    assert not credential_file_tokens(None), "no home means no values"
    assert not credential_file_tokens(tmp_path), "no file means no values"


def test_composition_root_collects_environment_and_file_tokens(tmp_path: Path) -> None:
    """The resolved Cargo environment carries every known token value."""
    _write_credentials(
        tmp_path, "credentials.toml", f'[registry]\ntoken = "{_FROM_FILE}"\n'
    )

    environment = cli_environment.cargo_environment({
        "CARGO_HOME": str(tmp_path),
        "CARGO_REGISTRY_TOKEN": _FROM_OVERRIDE,
        "CARGO_REGISTRY_DEFAULT": "crates-io",
    })

    assert set(environment.secrets) == {_FROM_FILE, _FROM_OVERRIDE}, (
        "both the environment and the credentials file must be collected"
    )
    assert environment.default_registry == "crates-io", "the registry must be read"
    assert environment.cargo_home == tmp_path, "CARGO_HOME must be honoured"


def test_credentials_file_value_is_hidden_from_relayed_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A value only the credentials file knew about is redacted from a child."""
    _write_credentials(
        tmp_path, "credentials.toml", f'[registry]\ntoken = "{_FROM_FILE}"\n'
    )
    environment = cli_environment.cargo_environment({"CARGO_HOME": str(tmp_path)})

    redacting_runner(SecretScope(environment.secrets))(_echoing(_FROM_FILE))

    assert _FROM_FILE not in capsys.readouterr().out, (
        "the credentials-file value must be hidden from the relay"
    )


def test_override_supplied_token_is_hidden_without_any_ambient_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pre-flight override token needs no matching process environment."""
    runner = redacting_runner(SecretScope())

    runner(
        _echoing(_FROM_OVERRIDE),
        env={"CARGO_REGISTRIES_INTERNAL_TOKEN": _FROM_OVERRIDE},
    )

    shown = capsys.readouterr().out
    assert _FROM_OVERRIDE not in shown, "the override's value must be hidden"
    assert REDACTED in shown, "and its place must show the redaction"


def test_digest_checked_runs_always_select_the_redacting_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no token yet, digests still select redaction; ordinary runs do not."""
    monkeypatch.delenv("LADING_USE_CMD_MOX_STUB", raising=False)
    monkeypatch.delenv("CARGO_REGISTRY_TOKEN", raising=False)

    assert cli._select_runner(SecretScope()) is not subprocess_runner, (
        "an expectation run must always redact"
    )
    assert cli._select_runner() is subprocess_runner, "an ordinary run is untouched"


def test_programmatic_default_runner_redacts_when_digests_are_expected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``publish.run`` wraps its own default runner without reading the environment."""
    environment = CargoEnvironment(None, None, (_FROM_NAMED,))
    checked = publish.PublishOptions(
        expect_digests=tmp_path / "digests.txt", cargo_environment=environment
    )

    publish_digest_run.command_runner(checked, SecretScope(environment.secrets))(
        _echoing(_FROM_NAMED)
    )

    assert _FROM_NAMED not in capsys.readouterr().out, (
        "the default runner must hide the secret from the relay"
    )
    assert publish_digest_run.command_runner(publish.PublishOptions(), None) is (
        publish_pipeline._invoke
    ), "without digests the default runner is unchanged"


def test_redacting_and_plain_runners_log_the_same_command(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redaction must not change the ``Running external command`` line."""
    caplog.set_level(logging.DEBUG, logger="lading.runtime.subprocess_runner")
    command = [sys.executable, "-c", "print('hello')"]
    env = {"PATH": "/usr/bin", "CARGO_REGISTRY_TOKEN": _FROM_OVERRIDE}

    def logged(runner: cabc.Callable[..., object]) -> list[str]:
        """Return the runtime logger's messages for one run of ``command``."""
        caplog.clear()
        runner(command, env=env, echo_stdout=False)
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == "lading.runtime.subprocess_runner"
        ]

    plain = logged(subprocess_runner)
    redacted = logged(redacting_runner(SecretScope((_FROM_FILE,))))

    assert redacted == plain, "both runners must log the same lines"
    assert any(line.startswith("Running external command") for line in redacted), (
        "the command line must be logged"
    )
    assert _FROM_OVERRIDE not in "".join(redacted), "the environment stays masked"


def test_longest_secret_wins_when_groups_are_combined(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A token in ``env`` that contains a known secret is replaced whole."""
    context = _AuxBuildContext(
        runner=redacting_runner(SecretScope()),
        env={"CARGO_REGISTRY_TOKEN": "abcdef"},
        secrets=("abc",),
    )

    redacting_runner(SecretScope(("abc",)))(
        _echoing("abcdef"), env={"CARGO_REGISTRY_TOKEN": "abcdef"}
    )

    assert context.hidden() == ("abcdef", "abc"), "longest first, deduplicated"
    assert capsys.readouterr().out == f"{REDACTED}\n", "the whole token is hidden"


def test_the_logged_working_directory_hides_the_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A token in the working directory's path is scrubbed from the log only."""
    caplog.set_level(logging.INFO, logger="lading.runtime.subprocess_runner")
    workdir = tmp_path / f"work-{_FROM_NAMED}"
    workdir.mkdir()
    script = [sys.executable, "-c", "import os;print(os.getcwd())"]

    _code, stdout, _stderr = redacting_runner(SecretScope((_FROM_NAMED,)))(
        script, cwd=workdir, echo_stdout=False
    )

    assert _FROM_NAMED not in caplog.text, "the logged cwd must be scrubbed"
    assert "Running external command" in caplog.text, "the command must still be logged"
    assert str(workdir.resolve()) in stdout, "the process must have run in the real cwd"
