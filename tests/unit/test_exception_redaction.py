"""Exceptions leaving a digest-checked scope are sanitized, type intact."""

import logging
import traceback
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import ExpectedDigestsError
from lading.exceptions import LadingError
from lading.utils.redaction import REDACTED, sanitize_exception, use_secrets
from tests.helpers.publish_digests import PublishRig

_LEAK = "exc-credential-44dd55ee66ff"


class _DetailedError(Exception):
    """An exception carrying rendered text in an attribute."""

    def __init__(self, message: str, detail: str) -> None:
        """Keep ``detail`` alongside the message."""
        super().__init__(message)
        self.detail = detail
        self.code = 7


class _StubbornError(Exception):
    """An exception whose text comes from somewhere redaction cannot reach."""

    def __init__(self) -> None:
        """Hide the text in a private slot-like attribute read by ``__str__``."""
        super().__init__("opaque")
        self._rendered = [f"stubborn {_LEAK}"]

    def __str__(self) -> str:
        """Render from the list, which the sanitizer does not walk."""
        return self._rendered[0]


def test_message_attributes_notes_and_chain_are_redacted_in_place() -> None:
    """The type and the non-sensitive attributes survive; the text does not."""
    cause = ValueError(f"root cause {_LEAK}")
    error = _DetailedError(f"outer {_LEAK}", f"detail {_LEAK}")
    error.add_note(f"note {_LEAK}")
    error.__cause__ = cause
    error.__context__ = OSError(2, f"missing {_LEAK}", f"/work/{_LEAK}")

    safe = sanitize_exception(error, (_LEAK,))

    rendered = "".join(traceback.format_exception(safe))
    assert safe is error, "a reconstructible exception is kept as it is"
    assert isinstance(safe, _DetailedError), "the type must be preserved"
    assert safe.code == 7, "non-sensitive attributes must be untouched"
    assert _LEAK not in rendered, "the formatted traceback must be clean"
    assert REDACTED in rendered, "the redaction must show"


def test_unreconstructible_exception_is_replaced_by_a_redacted_lading_error() -> None:
    """When the type renders text redaction cannot reach, it is replaced."""
    safe = sanitize_exception(_StubbornError(), (_LEAK,))

    assert isinstance(safe, LadingError), "the replacement must be a LadingError"
    assert _LEAK not in str(safe), "the replacement must be clean"
    assert safe.__cause__ is None, "it must be chained to nothing"


def _fail_inside_the_scope() -> None:
    """Raise an error quoting the secret from inside an active scope."""
    with use_secrets((_LEAK,)):
        message = f"leaving {_LEAK}"
        raise ValueError(message)


def test_exception_leaving_the_scope_is_sanitized() -> None:
    """The scope's exit path sanitizes what propagates out of it."""
    with pytest.raises(ValueError, match="redacted") as excinfo:
        _fail_inside_the_scope()

    assert _LEAK not in "".join(traceback.format_exception(excinfo.value)), (
        "the exception must be clean after the scope ends"
    )


def test_malformed_digest_line_reaches_the_caller_without_the_token(
    tmp_path: Path,
) -> None:
    """A digest-file error that quotes the line is clean, traceback included."""
    rig = PublishRig.create(tmp_path)
    digests = rig.root / "digests.txt"
    digests.write_text(f"not a digest {_LEAK}\n", encoding="utf-8")
    options = publish.PublishOptions(
        build_directory=rig.build_directory,
        command_runner=rig.cargo,
        expect_digests=digests,
        cargo_environment=CargoEnvironment(None, None, (_LEAK,)),
    )

    with pytest.raises(ExpectedDigestsError) as excinfo:
        publish.run(
            rig.root,
            config_module.LadingConfig(),
            rig.workspace(),
            options=options,
        )

    assert "Malformed digest entries" in str(excinfo.value), (
        "the failure must still be reported, with its type"
    )
    assert _LEAK not in "".join(traceback.format_exception(excinfo.value)), (
        "no token may remain in the formatted traceback"
    )


def test_start_log_and_validation_run_inside_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A token in the workspace path is hidden from the very first log line."""
    caplog.set_level(logging.INFO)
    rig = PublishRig.create(tmp_path / _LEAK)

    rig.run(
        monkeypatch,
        expect_digests=rig.digests_file(),
        environment=CargoEnvironment(None, None, (_LEAK,)),
    )

    assert "Starting publish workflow" in caplog.text, "the start log must appear"
    assert _LEAK not in caplog.text, "no record may show the token in the path"
