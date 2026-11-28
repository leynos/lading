"""Redaction at the sinks where text leaves lading, and at the named spots."""

import logging
import typing as typ
from pathlib import Path

import pytest

from lading import cli
from lading.commands import publish as publish_command
from lading.commands import publish_diagnostics, publish_sccache
from lading.commands.publish_archive_check import PACKAGED, create_verifier
from lading.commands.publish_digests import ExpectedDigests
from lading.commands.publish_errors import ArchiveUnavailableError
from lading.utils.redaction import REDACTED
from lading.utils.secret_scope import use_secrets
from lading.workspace import WorkspaceModelError
from tests.unit.publish.conftest import make_crate
from tests.unit.publish.sccache_doubles import WRAPPER, ScriptedRunner, payload

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_LEAK = "sink-credential-77aa88bb99cc"
_LOGGER = logging.getLogger("lading.test.sink")


def test_archive_lookup_error_hides_the_secret_even_across_the_truncation(
    tmp_path: Path,
) -> None:
    """``cargo metadata`` output in the error is redacted before it is cut."""
    crate = make_crate(tmp_path, "alpha")
    noisy = "x" * 190 + _LEAK

    def runner(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        """Fail like a ``cargo metadata`` that echoes the secret."""
        return 101, "", noisy

    verifier = create_verifier(
        ExpectedDigests("d", {"alpha-0.1.0.crate": "0" * 64}), runner
    )

    with use_secrets((_LEAK,)), pytest.raises(ArchiveUnavailableError) as excinfo:
        verifier.check_archive(crate, crate.root_path, PACKAGED)

    assert _LEAK[:8] not in str(excinfo.value), "no fragment of the secret may remain"
    assert REDACTED[:6] in str(excinfo.value), "the redaction must show"


def test_compiletest_diagnostics_hide_paths_and_file_tails(tmp_path: Path) -> None:
    """Artefact paths and ``.stderr`` tails are redacted before they are appended."""
    artefact_dir = tmp_path / _LEAK
    artefact_dir.mkdir()
    artefact = artefact_dir / "ui.stderr"
    artefact.write_text(f"error: {_LEAK}\n", encoding="utf-8")

    with use_secrets((_LEAK,)):
        message = publish_diagnostics._append_compiletest_diagnostics(
            "tests failed", f"see {artefact}", "", tail_lines=5
        )

    assert "Compiletest stderr artefacts" in message, "the diagnostics must remain"
    assert _LEAK not in message, "neither the path nor the tail may show the secret"


def test_sccache_statistics_log_hides_the_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The mirrored ``--show-stats`` text is redacted."""
    caplog.set_level(logging.INFO)
    runner = ScriptedRunner([payload(1, 1, 0)])
    runner_call = runner.__call__

    def leaky(
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Answer the text query with output that includes the secret."""
        code, out, err = runner_call(command, cwd=cwd, env=env, echo_stdout=echo_stdout)
        return code, out.replace("ghac", f"ghac {_LEAK}"), err

    session = publish_sccache.SccacheSession(
        wrapper=WRAPPER, runner=leaky, cwd=tmp_path
    )
    with use_secrets((_LEAK,)):
        session.begin()
        session.finish()

    assert "sccache statistics" in caplog.text, "the mirrored text must be logged"
    assert _LEAK not in caplog.text, "the secret must not be logged"


def test_log_sink_filter_hides_a_message_built_without_redaction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The handler filter works alone: nothing upstream redacted this message."""
    caplog.set_level(logging.INFO)

    with use_secrets((_LEAK,)):
        _LOGGER.info("formatted early %s", f"value={_LEAK}")
        built_early = f"f-string {_LEAK}"
        _LOGGER.error(built_early)
        failure = RuntimeError(f"failure quoting {_LEAK}")
        try:
            raise failure
        except RuntimeError:
            _LOGGER.exception("operation failed")
        _LOGGER.warning("with stack", stack_info=True, extra={"note": _LEAK})
    _LOGGER.info("after the scope %s", _LEAK)

    inside, after = caplog.text.rsplit("after the scope", 1)
    assert _LEAK not in inside, "no record inside the scope may show the secret"
    assert "RuntimeError" in inside, "the traceback must still be logged"
    assert REDACTED in inside, "the redaction must show"
    assert _LEAK in after, "outside the scope nothing is altered"


def _stub_publish_to_raise(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Make ``lading publish`` fail with ``error`` once it starts."""

    def fail(*_args: object, **_kwargs: object) -> str:
        """Raise the staged error."""
        raise error

    monkeypatch.setattr(publish_command, "run", fail)
    monkeypatch.setattr(cli, "load_workspace", lambda _, **__: None)
    monkeypatch.setenv("CARGO_REGISTRY_TOKEN", _LEAK)
    monkeypatch.delenv("LADING_USE_CMD_MOX_STUB", raising=False)


@pytest.mark.parametrize(
    "case",
    [
        (WorkspaceModelError(f"bad workspace {_LEAK}"), "Workspace error"),
        (RuntimeError(f"unexpected {_LEAK}"), "Unexpected error"),
    ],
    ids=["workspace_error", "unexpected_error"],
)
def test_cli_error_printer_hides_the_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[Exception, str],
) -> None:
    """A digest-checked run's top-level errors never print the token."""
    error, heading = case
    _stub_publish_to_raise(monkeypatch, error)

    status = cli.main([
        "--workspace-root",
        str(tmp_path),
        "publish",
        "--expect-digests",
        "digests.txt",
    ])

    seen = capsys.readouterr().err
    assert status == 1, "the command must fail"
    assert heading in seen, "the error must still be printed"
    assert _LEAK not in seen, "the token must be hidden"


def test_cli_error_printer_is_untouched_without_the_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Behaviour without ``--expect-digests`` is exactly as before."""
    _stub_publish_to_raise(monkeypatch, RuntimeError(f"unexpected {_LEAK}"))

    cli.main(["--workspace-root", str(tmp_path), "publish"])

    assert _LEAK in capsys.readouterr().err, (
        "nothing may be redacted without the option"
    )
