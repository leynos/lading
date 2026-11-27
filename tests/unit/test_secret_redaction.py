"""Tests for keeping registry credentials out of subprocess output."""

import itertools
import json
import logging
import sys
import traceback
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import PublishPreflightError
from lading.runtime import CommandSpawnError
from lading.runtime.env_redaction import log_subprocess_environment
from lading.runtime.redacting_runner import redacting_runner
from lading.utils.process import append_detail, command_detail, with_detail
from lading.utils.redaction import (
    REDACTED,
    SecretRedactor,
    redact_text,
    registry_token_values,
    use_secrets,
)
from lading.workspace.metadata import CargoMetadataInvocationError
from tests.helpers.hypothesis_settings import filesystem_property_settings
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
    """A value straddling two chunks cannot slip through."""
    text = f"before {_LEAK} after {_LEAK}!"
    redactor = SecretRedactor((_LEAK,))

    shown = "".join(
        redactor.feed(text[start : start + chunk_size])
        for start in range(0, len(text), chunk_size)
    )
    shown += redactor.flush()

    assert shown == f"before {REDACTED} after {REDACTED}!", (
        "every occurrence must be replaced whatever the chunk size"
    )


def _printing_command(exit_code: int) -> list[str]:
    """Return a command printing the token to both streams and exiting."""
    script = (
        "import sys;"
        f"print({_LEAK!r});print('err ' + {_LEAK!r}, file=sys.stderr);"
        f"sys.exit({exit_code})"
    )
    return [sys.executable, "-c", script]


def test_runner_hides_the_token_from_the_relay_but_captures_it_raw(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What lading shows is redacted; what it parses is exactly what was written."""
    runner = redacting_runner((_LEAK,))

    exit_code, stdout, stderr = runner(_printing_command(0))

    mirrored = capsys.readouterr()
    assert exit_code == 0, "the child must have run"
    assert _LEAK in stdout, "the captured stdout must be raw"
    assert _LEAK in stderr, "the captured stderr must be raw"
    for shown in (mirrored.out, mirrored.err):
        assert _LEAK not in shown, "the relay must not show the token"
        assert REDACTED in shown, "the relay must show the redaction"


def test_token_in_a_call_environment_is_hidden_from_the_relay(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A token passed through ``env`` is hidden without being pre-registered."""
    runner = redacting_runner(())

    runner(_printing_command(0), env={"CARGO_REGISTRY_TOKEN": _LEAK})

    assert _LEAK not in capsys.readouterr().out, "the call's own value must be hidden"


def test_captured_metadata_parses_even_when_it_contains_a_secret_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Redaction must not alter the data lading parses."""
    document = json.dumps({"target_directory": f"/work/{_LEAK}/target"})
    script = f"print({document!r})"
    runner = redacting_runner((_LEAK,))

    _code, stdout, _stderr = runner([sys.executable, "-c", script])

    assert json.loads(stdout)["target_directory"] == f"/work/{_LEAK}/target", (
        "the captured JSON must be exactly what the child wrote"
    )
    assert _LEAK not in capsys.readouterr().out, "yet the relay must still hide it"


def test_debug_environment_log_hides_a_token_copied_into_another_variable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Masking by value catches a token in an innocuously named variable."""
    caplog.set_level(logging.DEBUG)

    log_subprocess_environment(
        {"CARGO_REGISTRY_TOKEN": _LEAK, "MIRROR_URL": f"https://mirror/{_LEAK}/x"},
        (_LEAK,),
    )

    assert "MIRROR_URL" in caplog.text, "the variable itself must still be logged"
    assert _LEAK not in caplog.text, "neither variable may show the value"


def test_spawn_failure_hides_a_token_in_the_program_and_its_cause() -> None:
    """A token in an executable path does not leak through a spawn error."""
    runner = redacting_runner((_LEAK,))

    with pytest.raises(CommandSpawnError) as excinfo:
        runner([f"/definitely/missing/{_LEAK}/tool"], echo_stdout=False)

    rendered = "".join(traceback.format_exception(excinfo.value))
    assert _LEAK not in str(excinfo.value), "the message must be clean"
    assert _LEAK not in str(excinfo.value.reason), "the recorded cause must be clean"
    assert _LEAK not in rendered, "the chained traceback must be clean"
    assert REDACTED in str(excinfo.value), "the redaction must be visible"


def test_spawn_failure_without_a_secret_keeps_the_original_cause() -> None:
    """Nothing to hide means the error is exactly what it was."""
    runner = redacting_runner((_LEAK,))

    with pytest.raises(CommandSpawnError) as excinfo:
        runner(["/definitely/missing/tool"], echo_stdout=False)

    assert isinstance(excinfo.value.__cause__, FileNotFoundError), (
        "the original operating system error must stay chained"
    )


def test_failure_message_and_logs_never_contain_the_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cargo failure that printed the token raises a clean error."""
    caplog.set_level(logging.DEBUG)
    rig = PublishRig.create(tmp_path)
    environment = CargoEnvironment(None, None, (_LEAK,))
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
        rig.run(
            monkeypatch,
            command_runner=runner,
            expect_digests=rig.digests_file(),
            environment=environment,
        )

    seen = capsys.readouterr()
    assert ran, "the pipeline must have run the stand-in"
    assert _LEAK not in str(excinfo.value), "the raised error must be clean"
    assert REDACTED in str(excinfo.value), "the error must show the redaction"
    assert _LEAK not in caplog.text, "no log record may carry the token"
    assert _LEAK not in seen.out + seen.err, "no relayed stream may carry it"


def test_failure_formatters_hide_active_secrets_in_one_place() -> None:
    """Every message built from captured output goes through the shared helpers."""
    with use_secrets((_LEAK,)):
        detail = command_detail("", f"boom {_LEAK}")
        combined = with_detail("failed", f"out {_LEAK}", "")
        appended = append_detail(f"cmd --token={_LEAK}", "x")
        metadata = str(CargoMetadataInvocationError(101, "", f"bad {_LEAK}"))

    for text in (detail, combined, appended, metadata):
        assert _LEAK not in text, "no shared formatter may quote the secret"
        assert REDACTED in text, "each must show the redaction"
    assert _LEAK in command_detail("", f"boom {_LEAK}"), (
        "outside a digest-checked run nothing is altered"
    )


# A small alphabet that includes the placeholder's own characters and the first
# fallback fillers, so secrets overlap one another in prefixes and suffixes and
# also overlap both the default marker and the fallbacks chosen in its place.
_ALPHABET = "xyzq*#" + REDACTED
_TEXTS = st.text(alphabet=_ALPHABET, max_size=60)
_SECRETS = st.lists(
    st.text(alphabet=_ALPHABET, min_size=1, max_size=6), min_size=1, max_size=4
)
_CUTS = st.lists(st.integers(min_value=0, max_value=60), max_size=10)


def _chunks(text: str, cuts: list[int]) -> list[str]:
    """Split ``text`` at the given cut points."""
    points = sorted({min(cut, len(text)) for cut in cuts} | {0, len(text)})
    return [text[start:stop] for start, stop in itertools.pairwise(points)]


@given(text=_TEXTS, secrets=_SECRETS, cuts=_CUTS)
@filesystem_property_settings(max_examples=300)
def test_streaming_redaction_equals_redacting_the_whole(
    text: str, secrets: list[str], cuts: list[int]
) -> None:
    """Every chunking gives exactly ``redact_text`` of the whole input."""
    redactor = SecretRedactor(secrets)

    streamed = "".join(redactor.feed(chunk) for chunk in _chunks(text, cuts))
    streamed += redactor.flush()

    assert streamed == redact_text(text, secrets), "streaming must match the whole"
    assert all(secret not in streamed for secret in secrets), (
        "no complete known secret may remain in the output"
    )


@given(text=_TEXTS, secrets=_SECRETS)
@filesystem_property_settings(max_examples=200)
def test_redaction_does_not_depend_on_the_order_of_secrets(
    text: str, secrets: list[str]
) -> None:
    """The longest match wins at each position, whatever order secrets arrive in."""
    assert redact_text(text, secrets) == redact_text(text, list(reversed(secrets))), (
        "the secrets' order must not change the result"
    )


def test_a_longer_secret_that_completes_later_beats_a_shorter_prefix() -> None:
    """A chunk ending in a shorter secret waits to see if a longer one completes."""
    redactor = SecretRedactor(("abc", "abcdef"))

    streamed = redactor.feed("abc") + redactor.feed("def") + redactor.flush()

    assert streamed == REDACTED, "the longer secret must be replaced whole"
