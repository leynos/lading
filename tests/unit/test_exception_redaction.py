"""Exceptions leaving a digest-checked scope are sanitized, type intact."""

import logging
import shutil
import traceback
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import ExpectedDigestsError
from lading.commands.publish_manifest import PublishPreparationError
from lading.exceptions import LadingError
from lading.utils.exception_redaction import sanitize_exception
from lading.utils.redaction import REDACTED, redact_text, use_secrets
from tests.helpers.publish_digests import PublishRig

_LEAK = "exc-credential-44dd55ee66ff"


def _redact(text: str) -> str:
    """Replace the synthetic token in ``text``."""
    return redact_text(text, (_LEAK,))


class _DetailedError(Exception):
    """An exception carrying rendered text in an attribute."""

    def __init__(self, message: str, detail: str) -> None:
        """Keep ``detail`` alongside the message."""
        super().__init__(message)
        self.detail = detail
        self.code = 7


class _Holder:
    """An opaque object the sanitizer cannot see inside."""

    def __init__(self, text: str) -> None:
        """Keep ``text`` where only the owner's ``__str__`` reads it."""
        self.text = text


class _StubbornError(Exception):
    """An exception whose text comes from somewhere redaction cannot reach."""

    def __init__(self) -> None:
        """Hide the text inside an opaque holder."""
        super().__init__("opaque")
        self._holder = _Holder(f"stubborn {_LEAK}")

    def __str__(self) -> str:
        """Render from the holder, which the sanitizer does not walk."""
        return f"{self._holder.text}"


def test_message_attributes_notes_and_chain_are_redacted_in_place() -> None:
    """The type and the non-sensitive attributes survive; the text does not."""
    cause = ValueError(f"root cause {_LEAK}")
    error = _DetailedError(f"outer {_LEAK}", f"detail {_LEAK}")
    error.add_note(f"note {_LEAK}")
    error.__cause__ = cause
    error.__context__ = OSError(2, f"missing {_LEAK}", f"/work/{_LEAK}")

    safe = sanitize_exception(error, _redact)

    rendered = "".join(traceback.format_exception(safe))
    assert safe is error, "a reconstructible exception is kept as it is"
    assert isinstance(safe, _DetailedError), "the type must be preserved"
    assert safe.code == 7, "non-sensitive attributes must be untouched"
    assert _LEAK not in rendered, "the formatted traceback must be clean"
    assert REDACTED in rendered, "the redaction must show"


def test_unreconstructible_exception_is_replaced_by_a_redacted_lading_error() -> None:
    """When the type renders text redaction cannot reach, it is replaced."""
    safe = sanitize_exception(_StubbornError(), _redact)

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


def test_strings_nested_in_args_and_attributes_are_redacted_at_any_depth() -> None:
    """Containers inside ``args`` and attributes are rebuilt with clean strings."""
    error = _DetailedError("outer", "x")
    error.args = (
        ["outer", [(f"src/{_LEAK}", {f"k{_LEAK}": {f"v{_LEAK}"}})]],
        frozenset({f"f{_LEAK}"}),
    )

    safe = sanitize_exception(error, _redact)

    assert _LEAK not in repr(safe) + str(safe), "str and repr must be clean"
    assert safe is error, "a clean exception is sanitized in place"


def test_a_dirty_exception_in_the_chain_is_replaced_at_its_position() -> None:
    """A replaced cause keeps its type name and its place under the outer error."""
    outer = ValueError("outer")
    outer.__cause__ = _StubbornError()

    safe = sanitize_exception(outer, _redact)

    assert safe is outer, "the clean outer exception stays"
    assert isinstance(safe.__cause__, LadingError), "the dirty cause is replaced"
    assert "_StubbornError" in str(safe.__cause__), "it carries the original type name"
    assert _LEAK not in "".join(traceback.format_exception(safe)), "no token remains"


def _copytree_into_a_failing_destination(source: Path, destination: Path) -> None:
    """Raise a ``shutil.Error`` whose reasons quote token-bearing paths."""
    errors = [(str(source / _LEAK), str(destination / _LEAK), f"denied {_LEAK}")]
    failure = shutil.Error(errors)
    try:
        raise failure
    except shutil.Error as exc:
        message = "Cannot copy workspace into staging directory"
        raise PublishPreparationError(message) from exc


def test_shutil_error_chained_under_a_preparation_error_is_clean(
    tmp_path: Path,
) -> None:
    """The caller's formatted traceback shows no token from a nested reason list."""
    caught: list[BaseException] = []

    def leave_the_scope() -> None:
        """Run the failing copy inside an active scope."""
        with use_secrets((_LEAK,)):
            _copytree_into_a_failing_destination(tmp_path, tmp_path / "out")

    try:
        leave_the_scope()
    except PublishPreparationError as exc:
        caught.append(exc)

    assert caught, "the preparation error must reach the caller"
    assert _LEAK not in "".join(traceback.format_exception(caught[0])), (
        "nothing in the chain, including the shutil.Error's nested tuples, may show it"
    )


def test_handler_on_a_specific_logger_receives_only_redacted_records(
    tmp_path: Path,
) -> None:
    """Redaction happens at record creation, so any handler is covered."""
    seen: list[str] = []

    class _Collect(logging.Handler):
        """Collect formatted records."""

        def emit(self, record: logging.LogRecord) -> None:
            """Remember the rendered record, traceback included."""
            seen.append(self.format(record))

    handler = _Collect()
    target = logging.getLogger("lading.commands.publish")
    previous_level = target.level
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    rig = PublishRig.create(tmp_path / _LEAK)
    options = publish.PublishOptions(
        build_directory=rig.build_directory,
        command_runner=rig.cargo,
        expect_digests=rig.digests_file({"alpha": b"x"}),
        cargo_environment=CargoEnvironment(None, None, (_LEAK,)),
    )
    try:
        with pytest.raises(ExpectedDigestsError):
            publish.run(
                rig.root, config_module.LadingConfig(), rig.workspace(), options=options
            )
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)

    assert any("Starting publish workflow" in line for line in seen), (
        "the direct handler must receive the start log"
    )
    assert _LEAK not in "\n".join(seen), "and it must not show the token"
