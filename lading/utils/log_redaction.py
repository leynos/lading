"""Redact log records while a digest-checked run is active.

This module is a deliberate, isolated exception to the rule that a library does
not touch global state. Hiding a credential from the logs is only guaranteed if
it happens wherever a record can be created or formatted, and a record can come
from any logger and be handled by a handler attached anywhere, so three hooks
are used, each switched on only while a digest-checked run holds secrets:

- a :class:`RecordFactory` wrapping the record factory, which redacts the
  message (arguments applied), traceback and stack text as the record is
  created;
- a :class:`MakeRecordPatch` wrapping ``logging.Logger.makeRecord``, because
  ``extra`` fields are applied after the factory has run;
- a :class:`RedactingFilter` on the root logger's handlers (and on any handler
  :func:`protect_handler` is given later), as a further layer.

Everything here takes the redaction as a function, so the module depends only on
:mod:`lading.utils.exception_redaction`, and :func:`~lading.utils.redaction.use_secrets`
installs and removes the lot through one handle, :class:`LogRedaction`.

Both wrappers are removed, not merely disabled, when their scope ends, even if
scopes overlap rather than nest: a wrapper whose successor is still installed
stays in the chain, switched off, and the next removal skips over it.

Examples
--------
>>> import logging
>>> hook = LogRedaction(lambda text: text.replace("hunter2", "X"))
>>> logging.getLogRecordFactory() is not logging.LogRecord
True
>>> hook.remove()
>>> logging.getLogRecordFactory() is logging.LogRecord
True
"""

import collections.abc as cabc
import logging
import typing as typ

from lading.utils.exception_redaction import Redact, scrub_value

_STANDARD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__)
_LIVE: list[RedactingFilter] = []


def redact_record(record: logging.LogRecord, redact: Redact) -> None:
    """Render ``record`` and redact its message, traceback and stack text.

    The arguments are applied and cleared, and the traceback is formatted now
    and cached as ``exc_text``, so whatever handler formats the record later sees
    only redacted text.
    """
    record.msg = redact(record.getMessage())
    record.args = ()
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


def redact_extras(record: logging.LogRecord, redact: Redact) -> None:
    """Redact every ``extra`` field of ``record``, at any depth.

    ``Logger.makeRecord`` adds these after the record factory has run, so this
    runs on the complete record.
    """
    for name in set(vars(record)) - _STANDARD_ATTRIBUTES:
        setattr(record, name, scrub_value(getattr(record, name), redact))


class RedactingFilter(logging.Filter):
    """A handler filter that redacts every record it passes.

    Parameters
    ----------
    redact : Redact
        Replaces secrets in one string.
    """

    def __init__(self, redact: Redact) -> None:
        """Remember ``redact`` for every later record."""
        super().__init__("lading-redaction")
        self._redact = redact
        self._handlers: list[logging.Handler] = []

    def attach(self, handler: logging.Handler) -> None:
        """Install this filter on ``handler`` until :meth:`detach_all`."""
        handler.addFilter(self)
        self._handlers.append(handler)

    def detach_all(self) -> None:
        """Remove this filter from every handler it was attached to."""
        for handler in self._handlers:
            handler.removeFilter(self)
        self._handlers.clear()

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact ``record`` in place and let it through.

        Returns
        -------
        bool
            Always ``True``; the record is rewritten, never dropped.
        """
        redact_record(record, self._redact)
        return True


class _Hook(typ.Protocol):
    """What a wrapper exposes so a later removal can skip it."""

    previous: object

    def is_disabled(self) -> bool:
        """Whether the wrapper has been switched off."""
        raise NotImplementedError


def _unwrapped(previous: object) -> object:
    """Return what to reinstall: ``previous``, skipping wrappers switched off.

    A wrapper is either a hook object or a function tagged with its ``owner``
    hook; each exposes ``is_disabled`` and the ``previous`` it wrapped.

    Returns
    -------
    object
        The first wrapped value that is not a switched-off wrapper.
    """
    result = previous
    while hasattr(owner := getattr(result, "owner", result), "is_disabled"):
        hook = typ.cast("_Hook", owner)
        if not hook.is_disabled():
            break
        result = hook.previous
    return result


class _Switchable:
    """What both wrappers share: a switch, and the ``previous`` they wrapped."""

    previous: object

    def __init__(self, previous: object) -> None:
        """Wrap ``previous``, initially switched on."""
        self.previous = previous
        self._enabled = True

    def is_disabled(self) -> bool:
        """Whether this wrapper has been switched off."""
        return not self._enabled


class RecordFactory(_Switchable):
    """A log-record factory that redacts each record as it is created.

    Parameters
    ----------
    previous : Callable[..., logging.LogRecord]
        The factory being wrapped.
    redact : Redact
        Replaces secrets in one string.
    """

    def __init__(
        self, previous: cabc.Callable[..., logging.LogRecord], redact: Redact
    ) -> None:
        """Wrap ``previous``, redacting until :meth:`disable`."""
        super().__init__(previous)
        self._call_previous = previous
        self._redact = redact

    def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord:
        """Create a record with the wrapped factory, then redact it.

        Returns
        -------
        logging.LogRecord
            The redacted record.
        """
        record = self._call_previous(*args, **kwargs)
        if self._enabled:
            redact_record(record, self._redact)
        return record

    def disable(self) -> None:
        """Switch off, and unwrap if this is still the installed factory."""
        self._enabled = False
        if logging.getLogRecordFactory() is self:
            logging.setLogRecordFactory(
                typ.cast(
                    "cabc.Callable[..., logging.LogRecord]", _unwrapped(self.previous)
                )
            )


class MakeRecordPatch(_Switchable):
    """Wraps ``logging.Logger.makeRecord`` so ``extra`` fields are redacted too.

    Parameters
    ----------
    redact : Redact
        Replaces secrets in one string.
    """

    def __init__(self, redact: Redact) -> None:
        """Install the wrapper around the current ``Logger.makeRecord``."""
        previous: cabc.Callable[..., logging.LogRecord] = logging.Logger.makeRecord
        super().__init__(previous)
        self._call_previous: cabc.Callable[..., logging.LogRecord] = previous
        self._redact = redact

        def make_record(
            logger: logging.Logger, *args: object, **kwargs: object
        ) -> logging.LogRecord:
            """Make the record with the wrapped method, then redact it."""
            record = self._call_previous(logger, *args, **kwargs)
            if self._enabled:
                redact_extras(record, self._redact)
            return record

        make_record.owner = self  # ty: ignore[unresolved-attribute] -- tags the wrapper with its hook
        self._installed = make_record
        setattr(logging.Logger, "makeRecord", make_record)  # ruff: ignore[set-attr-with-constant] -- class-level wrap; direct assignment fails type checking

    def disable(self) -> None:
        """Switch off, and unwrap if this is still the installed method."""
        self._enabled = False
        if logging.Logger.makeRecord is self._installed:
            setattr(logging.Logger, "makeRecord", _unwrapped(self.previous))  # ruff: ignore[set-attr-with-constant] -- restores the wrapped method


class LogRedaction:
    """All three hooks, installed together and removed together.

    Parameters
    ----------
    redact : Redact
        Replaces secrets in one string.
    """

    def __init__(self, redact: Redact) -> None:
        """Install the filter, factory and ``makeRecord`` wrapper."""
        self._filter = RedactingFilter(redact)
        for handler in logging.getLogger().handlers:
            self._filter.attach(handler)
        _LIVE.append(self._filter)
        self._factory = RecordFactory(logging.getLogRecordFactory(), redact)
        logging.setLogRecordFactory(self._factory)
        self._patch = MakeRecordPatch(redact)

    def remove(self) -> None:
        """Remove every hook, restoring what was installed before."""
        self._patch.disable()
        self._factory.disable()
        _LIVE.remove(self._filter)
        self._filter.detach_all()


def protect_handler(handler: logging.Handler) -> None:
    """Put every live redaction filter on ``handler``.

    Logging is configured after the command-line entry point has activated its
    secrets, so a handler created then would otherwise miss the filter.
    """
    for redaction in _LIVE:
        redaction.attach(handler)
