"""Tests for hiding the credentials of the Cargo home each command runs with.

A relative ``CARGO_HOME`` is resolved by Cargo against each command's working
directory, so commands run from different workspace or staged directories can
read different credentials files. The redacting runner reads the file each
command will use before starting it.
"""

import logging
import sys
import typing as typ
from pathlib import Path

from lading.runtime.credentials import credential_file_tokens
from lading.runtime.redacting_runner import redacting_runner
from lading.utils.redaction import REDACTED
from lading.utils.secret_scope import SecretScope, redact_active, use_secrets

if typ.TYPE_CHECKING:
    import pytest

_FIRST = "cio-first-home-token-0123456789"
_SECOND = "cio-second-home-token-9876543210"
_HOME = Path("chome")
# Read the token from the credentials file relative to the child's own working
# directory and print it, as a misbehaving build script might. The token never
# appears on the command line, so only the file can reveal it.
_PRINT_FROM_OWN_HOME = (
    "import pathlib, tomllib;"
    "text = pathlib.Path('chome/credentials.toml').read_text(encoding='utf-8');"
    "print('token', tomllib.loads(text)['registry']['token'])"
)


def _directory_with_token(root: Path, name: str, token: str) -> Path:
    """Create ``root/name/chome/credentials.toml`` holding ``token``."""
    directory = root / name
    (directory / _HOME).mkdir(parents=True)
    (directory / _HOME / "credentials.toml").write_text(
        f'[registry]\ntoken = "{token}"\n', encoding="utf-8"
    )
    return directory


def test_a_command_hides_the_token_of_its_own_relative_cargo_home(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The second directory's token is hidden although the root never read it."""
    caplog.set_level(logging.DEBUG)
    first = _directory_with_token(tmp_path, "first", _FIRST)
    second = _directory_with_token(tmp_path, "second", _SECOND)
    # What the entry point collects from its own directory, the first one.
    collected = credential_file_tokens(first / _HOME, redact=str)
    logger = logging.getLogger("lading.commands.publish")

    with use_secrets(collected) as scope:
        runner = redacting_runner(scope, cargo_home=_HOME)
        exit_code, stdout, _stderr = runner(
            [sys.executable, "-c", _PRINT_FROM_OWN_HOME], cwd=second
        )
        logger.warning("cargo said: %s", stdout)
        detail = redact_active(stdout)

    relayed = capsys.readouterr().out
    assert exit_code == 0, "the child must have run"
    assert _SECOND in stdout, "the captured output lading parses stays raw"
    assert _SECOND not in relayed, "the relayed output must hide the token"
    assert f"token {REDACTED}" in relayed, "and show the redaction in its place"
    assert _SECOND not in caplog.text, "no log record may show the token"
    assert _SECOND not in detail, "a later diagnostic must hide it as well"
    assert redact_active(_SECOND) == _SECOND, "the scope's end forgets it again"


def test_a_command_cargo_home_overrides_the_entry_points(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``CARGO_HOME`` in the command's own environment is the one read."""
    second = _directory_with_token(tmp_path, "second", _SECOND)
    runner = redacting_runner(SecretScope(), cargo_home=Path("elsewhere"))
    script = (
        "import os, pathlib, tomllib;"
        "home = pathlib.Path(os.environ['CARGO_HOME']);"
        "text = (home / 'credentials.toml').read_text(encoding='utf-8');"
        "print('token', tomllib.loads(text)['registry']['token'])"
    )

    with use_secrets(()):
        runner(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env={"CARGO_HOME": str(second / _HOME), "PATH": ""},
        )

    assert _SECOND not in capsys.readouterr().out, "the overriding home is read"


def test_a_command_home_override_moves_its_default_cargo_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``CARGO_HOME``, the command's own ``HOME`` names its Cargo home."""
    home = tmp_path / "home"
    (home / ".cargo").mkdir(parents=True)
    (home / ".cargo" / "credentials.toml").write_text(
        f'[registry]\ntoken = "{_SECOND}"\n', encoding="utf-8"
    )
    runner = redacting_runner(SecretScope(), cargo_home=tmp_path / "entry" / ".cargo")
    script = (
        "import os, pathlib, tomllib;"
        "home = pathlib.Path(os.environ['HOME']) / '.cargo';"
        "text = (home / 'credentials.toml').read_text(encoding='utf-8');"
        "print('token', tomllib.loads(text)['registry']['token'])"
    )

    runner([sys.executable, "-c", script], cwd=tmp_path, env={"HOME": str(home)})

    assert _SECOND not in capsys.readouterr().out, "the overridden home is read"
