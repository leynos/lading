"""Failed auxiliary pre-flight commands must not echo credentials."""

import logging
import typing as typ
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish_preflight
from lading.commands.publish_errors import PublishPreflightError

from .conftest import _real_preflight

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_LEAK = "aux-credential-5e4d3c2b1a09"


def _request(
    *, secrets: tuple[str, ...], env_overrides: tuple[tuple[str, str], ...] = ()
) -> publish_preflight.PreflightRequest:
    """Return a pre-flight request whose auxiliary build quotes ``_LEAK``."""
    configuration = config_module.LadingConfig(
        preflight=config_module.PreflightConfig(
            aux_build=(("make", f"--token={_LEAK}"),), env_overrides=env_overrides
        )
    )

    def runner(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Succeed for git; fail the auxiliary build."""
        del cwd, env, echo_stdout
        return (1, "", "boom") if command[0] == "make" else (0, "", "")

    return publish_preflight.PreflightRequest(
        allow_dirty=True, configuration=configuration, runner=runner, secrets=secrets
    )


def test_known_secret_in_a_failed_auxiliary_argument_is_hidden(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Neither the error nor the log quotes a known secret from the arguments."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(PublishPreflightError) as excinfo:
        _real_preflight(tmp_path, _request(secrets=(_LEAK,)))

    assert "Auxiliary build command failed" in str(excinfo.value), (
        "the failure must still be reported"
    )
    assert _LEAK not in str(excinfo.value), "the error must not quote the secret"
    assert "<redacted>" in str(excinfo.value), "the error must show the redaction"
    assert _LEAK not in caplog.text, "the log must not quote the secret"


def test_override_supplied_registry_token_is_hidden_too(tmp_path: Path) -> None:
    """A token set through ``env_overrides`` needs no pre-registered secret."""
    request = _request(secrets=(), env_overrides=(("CARGO_REGISTRY_TOKEN", _LEAK),))

    with pytest.raises(PublishPreflightError) as excinfo:
        _real_preflight(tmp_path, request)

    assert _LEAK not in str(excinfo.value), "the override's value must be hidden"
