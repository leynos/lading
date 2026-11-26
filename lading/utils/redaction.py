r"""Keep known registry credentials out of what lading shows.

A build script, a test, or a Cargo diagnostic can print the value of
``CARGO_REGISTRY_TOKEN`` that lading's environment holds. Redaction here
protects what lading *shows*: the console relay, log lines, and the error
messages built from captured output. It never changes what lading *parses*:
captured streams stay exactly as the child wrote them, so a value that happens
to coincide with, say, ``cargo metadata`` JSON cannot corrupt it.

There are three layers. :class:`SecretRedactor` filters a stream of text
chunks, for the relay. :func:`use_secrets` and :func:`redact_active` carry the
known values for the duration of a digest-checked run, so the helpers that
embed captured output in messages
(:func:`lading.utils.process.command_detail` and
:func:`~lading.utils.process.append_detail`) redact in one place. Finally,
:func:`use_secrets` also installs :class:`RedactingFilter` on the logging
handlers for as long as secrets are active, so a message built without any
redaction, however it got there, is still hidden where it leaves lading. The
command line's error printer uses :func:`redact_active` the same way.

Redaction is deliberately narrow: it replaces the exact values of the registry
token variables lading was given, never a pattern.

Examples
--------
>>> redactor = SecretRedactor(("hunter2",))
>>> redactor.feed("password hun") + redactor.feed("ter2 done") + redactor.flush()
'password <redacted> done'
>>> registry_token_values({"CARGO_REGISTRY_TOKEN": "abc", "HOME": "/root"})
('abc',)
>>> with use_secrets(("abc",)):
...     redact_active("token abc")
'token <redacted>'
"""

import collections.abc as cabc
import contextlib
import contextvars
import functools
import logging
import re

from lading.utils.exception_redaction import sanitize_exception

REDACTED = "<redacted>"

# Cargo's two credential variables: the crates.io token and a named registry's.
_TOKEN_VARIABLE = re.compile(r"CARGO_REGISTRY_TOKEN|CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")
_LIVE: list[RedactingFilter] = []
_ACTIVE: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "lading_active_secrets", default=()
)


def _longest_first(values: cabc.Iterable[str]) -> tuple[str, ...]:
    """Return the distinct non-empty ``values``, longest first then alphabetical."""
    return tuple(
        sorted({v for v in values if v}, key=lambda value: (-len(value), value))
    )


def registry_token_values(environ: cabc.Mapping[str, object]) -> tuple[str, ...]:
    """Return the non-empty registry token values held in ``environ``.

    Parameters
    ----------
    environ : cabc.Mapping[str, object]
        An environment mapping, such as the one lading was started with.

    Returns
    -------
    tuple[str, ...]
        The distinct token values, longest first (then alphabetically) so a
        value that contains another is replaced whole.
    """
    return _longest_first(
        str(value) for key, value in environ.items() if _TOKEN_VARIABLE.fullmatch(key)
    )


def redact_text(text: str, secrets: cabc.Sequence[str]) -> str:
    """Return ``text`` with every exact secret value replaced.

    Returns
    -------
    str
        ``text`` with each of ``secrets`` replaced by a placeholder.

    Examples
    --------
    >>> redact_text("--token hunter2", ("hunter2",))
    '--token <redacted>'
    """
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


class RedactingFilter(logging.Filter):
    """A handler filter that hides ``secrets`` from every record it passes.

    It renders the message and rewrites the record so the arguments are already
    applied, then redacts the cached traceback and stack text too. Holding its
    own copy of the secrets, rather than reading the context variable, keeps it
    correct for records emitted from other threads.

    Parameters
    ----------
    secrets : cabc.Sequence[str]
        The values to replace.
    """

    def __init__(self, secrets: cabc.Sequence[str]) -> None:
        """Remember ``secrets`` for every later record."""
        super().__init__("lading-redaction")
        self._secrets = tuple(secrets)
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
        redact_record(record, self._secrets)
        return True


def redact_record(record: logging.LogRecord, secrets: cabc.Sequence[str]) -> None:
    """Render ``record`` and replace ``secrets`` in its message, traceback and stack.

    The arguments are applied and cleared, and the traceback is formatted now
    and cached as ``exc_text``, so whatever handler formats the record later
    sees only redacted text.
    """
    record.msg = redact_text(record.getMessage(), secrets)
    record.args = ()
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact_text(record.exc_text, secrets)
    if record.stack_info:
        record.stack_info = redact_text(record.stack_info, secrets)


class RecordFactory:
    """A log-record factory that redacts each record as it is created.

    Installed while secrets are active, it covers a handler attached directly to
    any logger, which a filter on the root handlers would miss. It wraps the
    factory it replaces and can be switched off when its scope ends, which keeps
    nested and overlapping scopes safe.

    Parameters
    ----------
    previous : Callable[..., logging.LogRecord]
        The factory being wrapped.
    secrets : cabc.Sequence[str]
        The values to replace.
    """

    def __init__(
        self,
        previous: cabc.Callable[..., logging.LogRecord],
        secrets: cabc.Sequence[str],
    ) -> None:
        """Wrap ``previous``, redacting ``secrets`` until :meth:`disable`."""
        self.previous = previous
        self._secrets = tuple(secrets)
        self._enabled = True

    def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord:
        """Create a record with the wrapped factory, then redact it.

        Returns
        -------
        logging.LogRecord
            The redacted record.
        """
        record = self.previous(*args, **kwargs)
        if self._enabled:
            redact_record(record, self._secrets)
        return record

    def disable(self) -> None:
        """Stop redacting, and unwrap if this is still the installed factory."""
        self._enabled = False
        if logging.getLogRecordFactory() is self:
            logging.setLogRecordFactory(self.previous)


@contextlib.contextmanager
def use_secrets(secrets: cabc.Iterable[str]) -> cabc.Generator[None]:
    """Hide ``secrets`` (in addition to any already active) from shown output.

    For the duration of the block the secrets are replaced by
    :func:`redact_active`, a :class:`RecordFactory` redacts every log record as
    it is created (so a handler on any logger is covered), and a
    :class:`RedactingFilter` is installed on the root logger's handlers. Both
    are removed afterwards, so nothing changes for a run that never activates
    any. An exception that propagates out of the block is sanitized first (see
    :func:`~lading.utils.exception_redaction.sanitize_exception`).
    """
    combined = _longest_first((*_ACTIVE.get(), *secrets))
    token = _ACTIVE.set(combined)
    redaction = RedactingFilter(combined)
    for handler in logging.getLogger().handlers:
        redaction.attach(handler)
    _LIVE.append(redaction)
    factory = RecordFactory(logging.getLogRecordFactory(), combined)
    logging.setLogRecordFactory(factory)
    try:
        yield
    except Exception as exc:
        # An exception leaving the scope is a sink too: it reaches callers, logs
        # and tracebacks after the scope has ended.
        safe = sanitize_exception(exc, functools.partial(redact_text, secrets=combined))
        if safe is exc:
            raise
        raise safe from safe.__cause__
    finally:
        factory.disable()
        _LIVE.remove(redaction)
        redaction.detach_all()
        _ACTIVE.reset(token)


def protect_handler(handler: logging.Handler) -> None:
    """Put every live redaction filter on ``handler``.

    Logging is configured after the command-line entry point has activated its
    secrets, so a handler created then would otherwise miss the filter.
    """
    for redaction in _LIVE:
        redaction.attach(handler)


def redact_active(text: str) -> str:
    """Return ``text`` with the currently active secrets replaced.

    Returns
    -------
    str
        ``text`` unchanged when no secrets are active.
    """
    return redact_text(text, _ACTIVE.get())


class SecretRedactor:
    """Replace exact secret values in a stream of text chunks.

    One instance serves one stream, because it carries the held-back tail
    between chunks: a value can straddle two reads, so the final
    ``len(longest) - 1`` characters wait for the next chunk to prove they do
    not begin a secret.

    Parameters
    ----------
    secrets : cabc.Sequence[str]
        The values to replace, longest first.
    """

    def __init__(self, secrets: cabc.Sequence[str]) -> None:
        """Remember ``secrets`` and start with nothing held back."""
        self._secrets = tuple(secrets)
        self._hold = max((len(secret) for secret in self._secrets), default=1) - 1
        self._pending = ""

    def feed(self, text: str) -> str:
        """Return the part of ``text`` that is safe to show now.

        Returns
        -------
        str
            Redacted text, excluding a tail that might begin a secret.
        """
        combined = redact_text(self._pending + text, self._secrets)
        keep = min(self._hold, len(combined))
        self._pending = combined[len(combined) - keep :]
        return combined[: len(combined) - keep]

    def flush(self) -> str:
        """Return whatever is still held back, once the stream has ended.

        Returns
        -------
        str
            The remaining redacted text.
        """
        remainder, self._pending = self._pending, ""
        return remainder
