"""Log hooks redact records at creation, and are restored when scopes end."""

import collections.abc as cabc
import contextlib
import contextvars
import logging
from pathlib import Path

import pytest

from lading import config as config_module
from lading.commands import publish
from lading.commands.publish_cargo_config import CargoEnvironment
from lading.commands.publish_errors import ExpectedDigestsError
from lading.utils.redaction import REDACTED, redact_text
from lading.utils.secret_scope import redact_active, use_secrets
from tests.helpers.publish_digests import PublishRig

_LEAK = "exc-credential-44dd55ee66ff"


def _redact(text: str) -> str:
    """Replace the synthetic token in ``text``."""
    return redact_text(text, (_LEAK,))


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


def test_extra_fields_are_redacted_for_a_handler_on_a_specific_logger() -> None:
    """``extra`` is applied after the record factory, and is covered too."""
    seen: list[str] = []

    class _Note(logging.Handler):
        """Render a formatter that reads the ``extra`` fields."""

        def emit(self, record: logging.LogRecord) -> None:
            """Remember the rendered record."""
            seen.append(self.format(record))

    handler = _Note()
    handler.setFormatter(logging.Formatter("%(message)s | %(note)s | %(nested)s"))
    target = logging.getLogger("lading.commands.publish")
    previous_level = target.level
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        with use_secrets((_LEAK,)):
            target.info(
                "event",
                extra={"note": f"token {_LEAK}", "nested": {"k": [f"v {_LEAK}"]}},
            )
        target.info("after", extra={"note": _LEAK, "nested": "x"})
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)

    assert _LEAK not in seen[0], "no extra field may show the token inside the scope"
    assert REDACTED in seen[0], "the redaction must show"
    assert _LEAK in seen[1], "outside the scope nothing is altered"
    assert logging.Logger.makeRecord.__name__ == "makeRecord", (
        "the original makeRecord must be restored"
    )


def test_nested_scopes_hide_a_longer_token_whole() -> None:
    """An outer secret that prefixes an inner one must not split it.

    Each scope's hooks wrap the previous ones, so the outer scope's hooks see
    the record first. If they used only the outer secret they would replace the
    prefix and leave the inner token's suffix in the output.
    """
    short = "token-0123456789abcdef"
    long = f"{short}9876543210"
    seen: list[str] = []

    class _Show(logging.Handler):
        """Render the message and the ``note`` extra."""

        def emit(self, record: logging.LogRecord) -> None:
            """Remember the rendered record."""
            seen.append(self.format(record))

    handler = _Show()
    handler.setFormatter(logging.Formatter("%(message)s | %(note)s"))
    target = logging.getLogger("lading.commands.publish")
    previous_level = target.level
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        with use_secrets((short,)), use_secrets((long,)):
            target.info("token %s", long, extra={"note": [f"also {long}"]})
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)

    assert "9876543210" not in seen[0], "no part of the longer token may show"
    assert seen[0] == f"token {REDACTED} | ['also {REDACTED}']", (
        "the message and extra must each show one marker per token"
    )


def test_nested_scope_filters_hide_a_longer_token_whole() -> None:
    """The stacked root-handler filters use the one combined set as well."""
    short = "token-0123456789abcdef"
    long = f"{short}9876543210"
    seen: list[str] = []

    class _Show(logging.Handler):
        """Render the message."""

        def emit(self, record: logging.LogRecord) -> None:
            """Remember the rendered record."""
            seen.append(self.format(record))

    handler = _Show()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with use_secrets((short,)), use_secrets((long,)):
            # Built directly, so only the handler filters can redact it.
            handler.handle(logging.makeLogRecord({"msg": f"token {long}"}))
    finally:
        root.removeHandler(handler)

    assert seen == [f"token {REDACTED}"], "the filters must hide the token whole"
    assert not handler.filters, "the filters must be removed with their scopes"


class _Unrenderable:
    """An object whose text cannot be produced."""

    def __str__(self) -> str:
        """Fail to render."""
        message = "no text"
        raise RuntimeError(message)


def test_rendered_objects_in_extra_fields_are_redacted() -> None:
    """A ``Path`` (or any object) that would render the token is replaced."""
    seen: list[str] = []

    class _Show(logging.Handler):
        """Display the ``note`` and ``odd`` extras."""

        def emit(self, record: logging.LogRecord) -> None:
            """Remember the rendered record."""
            seen.append(self.format(record))

    handler = _Show()
    handler.setFormatter(logging.Formatter("%(note)s | %(odd)s"))
    target = logging.getLogger("lading.commands.publish")
    previous_level = target.level
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        with use_secrets((_LEAK,)):
            target.info(
                "event", extra={"note": Path(f"/work/{_LEAK}"), "odd": _Unrenderable()}
            )
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)

    assert _LEAK not in seen[0], "the rendered Path must not show the token"
    assert "<unrenderable>" in seen[0], "an object that cannot render is replaced"
    assert REDACTED in seen[0], "the redaction must show"


def test_log_hooks_are_restored_after_nested_and_overlapping_scopes() -> None:
    """The process-wide logging hooks are exactly as before once scopes end."""
    original_factory = logging.getLogRecordFactory()
    original_make_record = logging.Logger.makeRecord
    outer = use_secrets((_LEAK,))
    inner = use_secrets(("other-secret-value",))

    # Nested: inner opens and closes inside outer.
    with outer:
        with inner:
            assert logging.getLogRecordFactory() is not original_factory, (
                "the factory must be wrapped while a scope is open"
            )
        assert logging.getLogRecordFactory() is not original_factory, (
            "the outer scope's factory must remain after the inner one ends"
        )
    assert logging.getLogRecordFactory() is original_factory, "nested: factory restored"
    assert logging.Logger.makeRecord is original_make_record, (
        "nested: makeRecord restored"
    )

    # Overlapping: the first scope closes while the second is still open.
    _run_overlapping_scopes(original_factory, original_make_record)


def _run_overlapping_scopes(
    original_factory: cabc.Callable[..., logging.LogRecord],
    original_make_record: object,
) -> None:
    """Close the first of two scopes while the second is open, then check.

    Each scope enters and exits in its own :class:`contextvars.Context`, as two
    tasks or threads would, so each ``ContextVar.reset`` restores only what its
    own scope changed and no secret stays active once both have ended.
    """
    before = redact_active(_LEAK)
    first, second = use_secrets((_LEAK,)), use_secrets(("another-secret-value",))
    first_context, second_context = (
        contextvars.copy_context(),
        contextvars.copy_context(),
    )
    open_scopes: list[
        tuple[contextvars.Context, contextlib.AbstractContextManager[object]]
    ] = []
    try:
        for context, scope in ((first_context, first), (second_context, second)):
            context.run(scope.__enter__)
            open_scopes.append((context, scope))
        while open_scopes:
            context, scope = open_scopes.pop(0)
            context.run(scope.__exit__, None, None, None)
    finally:
        for context, scope in open_scopes:
            context.run(scope.__exit__, None, None, None)

    assert logging.getLogRecordFactory() is original_factory, (
        "overlapping: factory restored"
    )
    assert logging.Logger.makeRecord is original_make_record, (
        "overlapping: makeRecord restored"
    )
    assert redact_active(_LEAK) == before == _LEAK, (
        "no secret may stay active once both scopes have ended"
    )
