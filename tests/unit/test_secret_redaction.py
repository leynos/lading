"""Tests for keeping registry credentials out of subprocess output."""

import io
import logging
import sys
import typing as typ

import pytest

from lading.commands.publish_errors import PublishPreflightError
from lading.runtime.redacting_runner import redacting_runner
from lading.runtime.redaction import (
    REDACTED,
    RedactingReader,
    guard_stream,
    registry_token_values,
)
from tests.helpers.publish_digests import PublishRig

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_LEAK = "cio-synthetic-token-1234567890"


def test_only_registry_token_variables_are_collected() -> None:
    """Exact credential variables count; look-alikes and empties do not."""
    environ = {
        "CARGO_REGISTRY_TOKEN": "a-token",
        "CARGO_REGISTRIES_INTERNAL_TOKEN": "b-token",
        "CARGO_REGISTRIES_X_Y_TOKEN": "",
        "CARGO_REGISTRIES_TOKEN": "not-a-registry-variable",
        "GITHUB_TOKEN": "other",
        "PATH": "/usr/bin",
    }

    assert registry_token_values(environ) == ("a-token", "b-token"), (
        "only the two Cargo credential shapes are collected"
    )


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 64])
def test_secret_split_across_reads_is_still_replaced(chunk_size: int) -> None:
    """A value straddling two pipe reads cannot slip through."""
    source = io.BytesIO(f"before {_LEAK} after {_LEAK}!".encode())
    reader = RedactingReader(source, (_LEAK,))

    output = b"".join(iter(lambda: reader.read(chunk_size), b""))

    assert output.decode() == f"before {REDACTED} after {REDACTED}!", (
        "every occurrence must be replaced whatever the read size"
    )


def test_stream_without_secrets_is_not_wrapped() -> None:
    """With nothing to hide the original stream is used untouched."""
    source = io.BytesIO(b"x")

    assert guard_stream(source, ()) is source, "no secrets, no wrapper"
    assert guard_stream(None, (_LEAK,)) is None, "a missing stream stays missing"


def _printing_command(exit_code: int) -> list[str]:
    """Return a command printing the token to both streams and exiting."""
    script = (
        "import sys;"
        f"print({_LEAK!r});print('err ' + {_LEAK!r}, file=sys.stderr);"
        f"sys.exit({exit_code})"
    )
    return [sys.executable, "-c", script]


def test_runner_hides_the_token_from_relay_and_capture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A child printing the token reaches neither the mirror nor the capture."""
    runner = redacting_runner((_LEAK,))

    exit_code, stdout, stderr = runner(_printing_command(0))

    mirrored = capsys.readouterr()
    assert exit_code == 0, "the child must have run"
    for text in (stdout, stderr, mirrored.out, mirrored.err):
        assert _LEAK not in text, "the token must not appear anywhere"
    assert REDACTED in stdout, "the capture must say something was redacted"
    assert REDACTED in mirrored.err, "the relayed stderr must say so too"


def test_token_in_a_call_environment_is_hidden_too() -> None:
    """A token passed through ``env`` is redacted without being pre-registered."""
    runner = redacting_runner(())

    _code, stdout, _stderr = runner(
        _printing_command(0),
        env={"CARGO_REGISTRY_TOKEN": _LEAK},
        echo_stdout=False,
    )

    assert _LEAK not in stdout, "the call's own token value must be hidden"


def test_failure_message_and_logs_never_contain_the_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cargo failure that printed the token raises a clean error."""
    caplog.set_level(logging.DEBUG)
    rig = PublishRig.create(tmp_path)
    real = redacting_runner((_LEAK,))
    ran: list[str] = []

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Stand in for cargo with a child that prints the token and fails."""
        ran.append(command[1])
        return real(_printing_command(1), cwd=cwd, env=env, echo_stdout=echo_stdout)

    with pytest.raises(PublishPreflightError) as excinfo:
        rig.run(monkeypatch, command_runner=runner)

    seen = capsys.readouterr()
    assert ran, "the pipeline must have run the stand-in"
    assert _LEAK not in str(excinfo.value), "the raised error must be clean"
    assert REDACTED in str(excinfo.value), "the error must show the redaction"
    assert _LEAK not in caplog.text, "no log record may carry the token"
    assert _LEAK not in seen.out + seen.err, "no relayed stream may carry it"
