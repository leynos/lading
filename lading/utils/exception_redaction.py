"""Hide secrets from exceptions, however deeply they hold rendered text.

An exception reaches callers, logs and tracebacks long after the code that
built it, and can carry text far from its message: nested inside ``args`` (as
:class:`shutil.Error` does with a list of ``(source, destination, reason)``
tuples), in attributes, in notes, and in the exceptions it is chained to.
:func:`sanitize_exception` rewrites all of that in place, so the type and the
non-sensitive attributes callers rely on are untouched, then checks what each
exception in the chain *renders* (``str`` and ``repr``). One that still shows a
secret, because its type builds its text from something unreachable, is
replaced at its position in the chain by a redacted
:class:`~lading.exceptions.LadingError` carrying the original type name.

The module takes the redaction as a function, so it depends on nothing else in
:mod:`lading.utils`.

Examples
--------
>>> error = ValueError(["path", ("a", "token hunter2")])
>>> safe = sanitize_exception(error, lambda text: text.replace("hunter2", "X"))
>>> safe is error, error.args
(True, (['path', ('a', 'token X')],))
"""

import collections.abc as cabc
import typing as typ

from lading.exceptions import LadingError

type Redact = cabc.Callable[[str], str]
type Memo = dict[int, object]

_TEXT_ATTRIBUTES = ("strerror", "filename", "filename2")


def _scrub(value: object, redact: Redact, memo: Memo) -> object:
    """Return ``value`` with every string redacted, at any depth.

    Returns
    -------
    object
        The redacted value; an exception is cleaned (possibly replaced).
    """
    match value:
        case str():
            result: object = redact(value)
        case BaseException():
            result = _clean(value, redact, memo)
        case _:
            result = _scrub_container(value, redact, memo)
    return result


def _scrub_container(value: object, redact: Redact, memo: Memo) -> object:
    """Rebuild the built-in containers with their contents redacted.

    An unknown type is returned as it is and left to the rendering check.

    Returns
    -------
    object
        The rebuilt container, or ``value`` unchanged.
    """

    def items(sequence: cabc.Iterable[object]) -> list[object]:
        """Redact each element of ``sequence``."""
        return [_scrub(item, redact, memo) for item in sequence]

    if id(value) in memo:
        return memo[id(value)]  # A container that contains itself.
    match value:
        case list():
            result: object = []
            memo[id(value)] = result
            typ.cast("list[object]", result).extend(items(value))
        case dict():
            result = {}
            memo[id(value)] = result
            typ.cast("dict[object, object]", result).update(
                zip(items(value), items(value.values()), strict=True)
            )
        case tuple() if hasattr(value, "_fields"):
            result = type(value)(*items(value))
        case tuple():
            result = tuple(items(value))
        case set() | frozenset():
            result = typ.cast("cabc.Callable[[list[object]], object]", type(value))(
                items(value)
            )
        case _:
            result = _scrub_object(value, redact)
    return result


def _scrub_object(value: object, redact: Redact) -> object:
    """Return ``value``, or its redacted text when it renders a secret.

    An object of a type this does not rebuild (a ``Path``, say) can still show
    a secret when formatted. If ``str`` or ``repr`` would, it is replaced by its
    redacted ``str``; one that cannot render at all becomes ``<unrenderable>``.

    Returns
    -------
    object
        ``value`` itself when it renders clean, otherwise a redacted string.
    """
    try:
        is_clean = _renders_clean(value, redact)
        text = f"{value}"
    except Exception:  # ruff: ignore[blind-except] -- a value that cannot render itself
        return "<unrenderable>"
    return value if is_clean else redact(text)


def _scrub_fields(exc: BaseException, redact: Redact, memo: Memo) -> None:
    """Redact ``exc``'s ``args``, text and instance attributes, and notes."""
    exc.args = typ.cast("tuple[object, ...]", _scrub(exc.args, redact, memo))
    for name in dict.fromkeys((*_TEXT_ATTRIBUTES, *vars(exc))):
        value = getattr(exc, name, None)
        if value is not None:
            setattr(exc, name, _scrub(value, redact, memo))
    notes = getattr(exc, "__notes__", None)
    if notes:
        exc.__notes__ = [redact(str(note)) for note in notes]


def _renders_clean(value: object, redact: Redact) -> bool:
    """Whether ``str`` and ``repr`` of ``value`` are unchanged by redaction."""
    return all(redact(text) == text for text in (str(value), repr(value)))


def _clean(exc: BaseException, redact: Redact, memo: Memo) -> BaseException:
    """Return ``exc`` sanitized in place, or its redacted replacement."""
    if id(exc) in memo:
        return typ.cast("BaseException", memo[id(exc)])
    memo[id(exc)] = exc  # Provisional, so a cycle in the chain terminates.
    cause: BaseException | None = None
    context: BaseException | None = None
    try:
        _scrub_fields(exc, redact, memo)
        cause = _clean_link(exc.__cause__, redact, memo)
        context = _clean_link(exc.__context__, redact, memo)
        suppress = exc.__suppress_context__
        exc.__cause__, exc.__context__ = cause, context
        exc.__suppress_context__ = suppress
        is_clean = _renders_clean(exc, redact)
    except Exception:  # ruff: ignore[blind-except] -- any failure must not let the original escape
        is_clean = False
    if is_clean:
        return exc
    replacement = LadingError(_describe(exc, redact))
    replacement.__cause__ = cause or context
    replacement.__traceback__ = exc.__traceback__
    memo[id(exc)] = replacement
    return replacement


def _describe(exc: BaseException, redact: Redact) -> str:
    """Return ``"<TypeName>: <redacted text>"`` for ``exc``, however it renders."""
    try:
        text = f"{exc}"
    except Exception:  # ruff: ignore[blind-except] -- an exception that cannot render itself
        text = "<unrenderable>"
    return redact(f"{type(exc).__name__}: {text}")


def _clean_link(
    link: BaseException | None, redact: Redact, memo: Memo
) -> BaseException | None:
    """Clean one link of a chain, keeping an absent link absent."""
    return None if link is None else _clean(link, redact, memo)


def sanitize_exception(exc: Exception, redact: Redact) -> Exception:
    """Hide secrets from ``exc`` and every exception chained to it.

    Parameters
    ----------
    exc : Exception
        The exception to sanitize; rewritten in place where it can be.
    redact : Redact
        Replaces secrets in one string.

    Returns
    -------
    Exception
        ``exc`` itself when everything it renders is clean, otherwise a
        redacted ``LadingError`` carrying its type name.
    """
    memo: Memo = {}
    result = _clean(exc, redact, memo)
    _relink(result, memo, set())
    return typ.cast("Exception", result)


def _relink(exc: BaseException, memo: Memo, seen: set[int]) -> None:
    """Point every chain link at its final exception, not a provisional original.

    While a cycle is being cleaned, a link can be recorded against an exception
    that is replaced later; this walks the finished chain and corrects those.
    """
    if id(exc) in seen:
        return
    seen.add(id(exc))
    suppress = exc.__suppress_context__
    for name in ("__cause__", "__context__"):
        link = getattr(exc, name)
        if link is None:
            continue
        final = typ.cast("BaseException", memo.get(id(link), link))
        if final is not link:
            setattr(exc, name, final)
        _relink(final, memo, seen)
    exc.__suppress_context__ = suppress


def scrub_value(value: object, redact: Redact) -> object:
    """Redact every string in ``value``, at any depth and through cycles.

    Returns
    -------
    object
        The redacted value (containers are rebuilt, exceptions cleaned).
    """
    return _scrub(value, redact, {})
