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
:func:`use_secrets` installs the log hooks of :mod:`lading.utils.log_redaction`
for as long as secrets are active, so a message built without any redaction,
however it got there, is still hidden where it leaves lading. The command
line's error printer uses :func:`redact_active` the same way.

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
import re

from lading.utils.exception_redaction import sanitize_exception
from lading.utils.log_redaction import LogRedaction

REDACTED = "<redacted>"

# Cargo's two credential variables: the crates.io token and a named registry's.
_TOKEN_VARIABLE = re.compile(r"CARGO_REGISTRY_TOKEN|CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")
_ACTIVE: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "lading_active_secrets", default=()
)


def ordered_secrets(values: cabc.Iterable[str]) -> tuple[str, ...]:
    """Return the distinct non-empty ``values``, longest first then alphabetical.

    Combine every group of secrets through this before replacing, so the longest
    value wins wherever one contains another.

    Returns
    -------
    tuple[str, ...]
        The ordered, deduplicated values.

    Examples
    --------
    >>> ordered_secrets(["ab", "", "abc", "ab"])
    ('abc', 'ab')
    """
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
    return ordered_secrets(
        str(value) for key, value in environ.items() if _TOKEN_VARIABLE.fullmatch(key)
    )


@functools.lru_cache(maxsize=64)
def _pattern(secrets: tuple[str, ...]) -> re.Pattern[str] | None:
    """Return one pattern matching any of ``secrets``, longest alternative first."""
    ordered = ordered_secrets(secrets)
    return re.compile("|".join(map(re.escape, ordered))) if ordered else None


def redact_text(text: str, secrets: cabc.Sequence[str]) -> str:
    """Return ``text`` with every exact secret value replaced.

    Scans left to right and, at each position, replaces the longest secret that
    starts there. That definition does not depend on the order secrets are
    given in, and a stream can reproduce it chunk by chunk
    (:class:`SecretRedactor`).

    Returns
    -------
    str
        ``text`` with each match replaced by a placeholder.

    Examples
    --------
    >>> redact_text("--token hunter2", ("hunter2",))
    '--token <redacted>'
    >>> redact_text("abcdef", ("abc", "abcdef"))
    '<redacted>'
    """
    pattern = _pattern(tuple(secrets))
    return text if pattern is None else pattern.sub(REDACTED, text)


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
    combined = ordered_secrets((*_ACTIVE.get(), *secrets))
    token = _ACTIVE.set(combined)
    hooks = LogRedaction(functools.partial(redact_text, secrets=combined))
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
        hooks.remove()
        _ACTIVE.reset(token)


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

    The output is identical to :func:`redact_text` on the whole input, whatever
    the chunking. To achieve that it keeps the last ``len(longest) - 1``
    characters raw until later input shows whether they begin a longer match;
    replacement only ever runs on text that no later chunk can change.

    One instance serves one stream, because it carries that held-back text.

    Parameters
    ----------
    secrets : cabc.Sequence[str]
        The values to replace, in any order.
    """

    def __init__(self, secrets: cabc.Sequence[str]) -> None:
        """Remember ``secrets`` and start with nothing held back."""
        self._secrets = tuple(secrets)
        self._pattern = _pattern(self._secrets)
        longest = max((len(secret) for secret in secrets), default=0)
        self._hold = max(longest - 1, 0)
        self._pending = ""

    def feed(self, text: str) -> str:
        """Return the part of ``text`` that is safe to show now.

        Returns
        -------
        str
            Redacted text, excluding a raw tail that might begin a match.
        """
        buffer = self._pending + text
        if self._pattern is None:
            self._pending = ""
            return buffer
        limit = len(buffer) - self._hold
        shown: list[str] = []
        consumed = 0
        for match in self._pattern.finditer(buffer):
            if match.start() >= limit:
                break
            shown += [buffer[consumed : match.start()], REDACTED]
            consumed = match.end()
        shown.append(buffer[consumed : max(consumed, limit)])
        self._pending = buffer[max(consumed, limit) :]
        return "".join(shown)

    def flush(self) -> str:
        """Return whatever is still held back, once the stream has ended.

        Returns
        -------
        str
            The remaining text, redacted.
        """
        remainder, self._pending = self._pending, ""
        return redact_text(remainder, self._secrets)
