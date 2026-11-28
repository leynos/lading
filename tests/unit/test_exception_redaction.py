"""Exceptions leaving a digest-checked scope are sanitized, type intact."""

import collections
import shutil
import traceback
import typing as typ
from pathlib import Path

import pytest

from lading.commands.publish_manifest import PublishPreparationError
from lading.exceptions import LadingError
from lading.utils.exception_redaction import sanitize_exception
from lading.utils.redaction import REDACTED, redact_text
from lading.utils.secret_scope import use_secrets

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


def test_self_referential_containers_terminate_and_stay_cyclic() -> None:
    """A list that contains itself is rebuilt with the cycle intact."""
    cyclic: list[object] = [f"token {_LEAK}"]
    cyclic.append(cyclic)
    error = RuntimeError("boom", cyclic)

    safe = sanitize_exception(error, _redact)

    rebuilt = safe.args[1]
    assert _LEAK not in "".join(traceback.format_exception(safe)), "no token remains"
    assert rebuilt[1] is rebuilt, "the cycle must be preserved in the rebuilt list"


_Pair = collections.namedtuple("_Pair", "first second")


class _Unrebuildable(_Pair):
    """A named tuple that cannot be reconstructed, so traversal must fail."""

    __slots__ = ()

    def __new__(cls, first: object, second: object) -> typ.Self:
        """Refuse reconstruction, as a type with a strict constructor would."""
        message = f"cannot rebuild {first!r} {second!r}"
        raise TypeError(message)


def test_failed_traversal_replaces_the_exception_instead_of_leaking_it() -> None:
    """If rebuilding fails, the redacted replacement is raised, not the original."""
    error = ValueError(tuple.__new__(_Unrebuildable, (f"token {_LEAK}", 1)))

    safe = sanitize_exception(error, _redact)

    assert isinstance(safe, LadingError), "the replacement must be a LadingError"
    assert "ValueError" in str(safe), "it must carry the original type name"
    assert _LEAK not in str(safe), "and it must be clean"


def test_a_replaced_exception_in_a_cycle_is_never_reached_through_its_original() -> (
    None
):
    """Chain links resolve to final replacements, not provisional originals."""
    dirty = _StubbornError()
    clean = ValueError("clean")
    dirty.__cause__ = clean
    clean.__cause__ = dirty

    safe = sanitize_exception(dirty, _redact)

    seen: list[BaseException] = []
    link: BaseException | None = safe
    while link is not None and link not in seen:
        seen.append(link)
        link = link.__cause__
    assert isinstance(safe, LadingError), "the dirty exception is replaced"
    assert all(item is not dirty for item in seen), "the original must be unreachable"
    assert _LEAK not in "".join(traceback.format_exception(safe)), "no token remains"
