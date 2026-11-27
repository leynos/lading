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
import dataclasses as dc
import functools
import itertools
import re

from lading.utils.exception_redaction import sanitize_exception
from lading.utils.log_redaction import LogRedaction

REDACTED = "<redacted>"

# Cargo's two credential variables: the crates.io token and a named registry's.
_TOKEN_VARIABLE = re.compile(r"CARGO_REGISTRY_TOKEN|CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")


@dc.dataclass(eq=False, slots=True)
class _Scope:
    """One open :func:`use_secrets` block: its secrets (which may grow) and parent."""

    secrets: tuple[str, ...]
    parent: _Scope | None


# The innermost open scope in this context.
_ACTIVE: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
    "lading_active_secrets", default=None
)
# Every open scope. The log hooks are process-wide, so they read this rather
# than the per-context ``_ACTIVE``.
_LOGGED: list[_Scope] = []


def _overlaps_marker(secret: str) -> bool:
    """Whether ``secret`` could appear in output through the default marker.

    True when the secret lies inside the marker, contains it, or starts with a
    proper suffix or ends with a proper prefix of it (so the marker plus
    adjacent text could spell it).

    Returns
    -------
    bool
        Whether the secret overlaps the marker.
    """
    marker = REDACTED
    inner = range(1, len(marker))
    return (
        secret in marker
        or marker in secret
        or any(secret.startswith(marker[-size:]) for size in inner)
        or any(secret.endswith(marker[:size]) for size in inner)
    )


# ``*`` reads best as a mask, then the other printable symbols. Past those every
# printable code point is tried, skipping the surrogates, which cannot be
# encoded.
_PREFERRED_FILLERS = "*#~=-_.+!$%&@^|:;?/,'\"`()[]{}<>"


def _filler_candidates() -> cabc.Iterator[str]:
    """Yield every candidate filler character in order of preference."""
    yield from _PREFERRED_FILLERS
    yield from map(chr, range(0x21, 0xD800))
    yield from map(chr, range(0xE000, 0x110000))


def _absent_character(secrets: cabc.Collection[str]) -> str:
    """Return the most preferred character that appears in none of ``secrets``.

    Returns
    -------
    str
        A single character absent from every secret.

    Raises
    ------
    ValueError
        If the secrets use every code point, so no marker can avoid them.
    """
    used = set().union(*secrets)
    absent = next((c for c in _filler_candidates() if c not in used), None)
    if absent is None:
        message = "the secrets use every character, so no marker can hide them"
        raise ValueError(message)
    return absent


def marker_for(secrets: cabc.Iterable[str]) -> str:
    """Return the replacement marker that cannot overlap any of ``secrets``.

    The default ``<redacted>`` is used unless a secret could overlap it (a
    named-registry token can be any string, even ``redacted``). Then the marker
    is built from one character found in none of the secrets, framed by ``<``
    and ``>`` when neither appears in any secret. Any overlap between a secret
    and the marker would have to share one of the marker's characters, so no
    secret can show through it. The choice depends only on the set of secrets,
    so streaming and one-shot redaction pick the same marker.

    Returns
    -------
    str
        The marker to put in place of each secret.

    Examples
    --------
    >>> marker_for(["token"])
    '<redacted>'
    >>> marker_for(["redacted"])
    '<********>'
    >>> marker_for(["<redacted>"])
    '**********'
    """
    known = ordered_secrets(secrets)
    if not any(map(_overlaps_marker, known)):
        return REDACTED
    filler = _absent_character(known)
    if any("<" in secret or ">" in secret for secret in known):
        return filler * len(REDACTED)
    return f"<{filler * (len(REDACTED) - 2)}>"


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


@dc.dataclass(frozen=True, slots=True)
class _Plan:
    """What replacing a set of secrets takes: one pattern and its marker."""

    pattern: re.Pattern[str]
    marker: str


@functools.lru_cache(maxsize=64)
def _plan(secrets: tuple[str, ...]) -> _Plan | None:
    """Return the pattern (longest alternative first) and marker for ``secrets``."""
    ordered = ordered_secrets(secrets)
    if not ordered:
        return None
    return _Plan(re.compile("|".join(map(re.escape, ordered))), marker_for(ordered))


def redact_text(text: str, secrets: cabc.Sequence[str]) -> str:
    """Return ``text`` with every exact secret value replaced.

    Scans left to right and, at each position, replaces the longest secret that
    starts there. That definition does not depend on the order secrets are
    given in, and a stream can reproduce it chunk by chunk
    (:class:`SecretRedactor`). The replacement is ``<redacted>`` unless a secret
    could overlap it (see :func:`marker_for`).

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
    >>> "redacted" in redact_text("a redacted b", ("redacted",))
    False
    """
    plan = _plan(tuple(secrets))
    return text if plan is None else plan.pattern.sub(lambda _m: plan.marker, text)


@contextlib.contextmanager
def use_secrets(secrets: cabc.Iterable[str]) -> cabc.Generator[None]:
    """Hide ``secrets`` (in addition to any already active) from shown output.

    For the duration of the block the secrets are replaced by
    :func:`redact_active`, a :class:`RecordFactory` redacts every log record as
    it is created (so a handler on any logger is covered), and a
    :class:`RedactingFilter` is installed on the root logger's handlers. Both
    are removed afterwards, so nothing changes for a run that never activates
    any. The hooks redact with the secrets of every open scope at once (see
    :func:`_redact_logged`). An exception that propagates out of the block is
    sanitized first (see
    :func:`~lading.utils.exception_redaction.sanitize_exception`).
    """
    parent = _ACTIVE.get()
    scope = _Scope(ordered_secrets((*_active_secrets(), *secrets)), parent)
    token = _ACTIVE.set(scope)
    _LOGGED.append(scope)
    hooks = LogRedaction(_redact_logged)
    try:
        yield
    except Exception as exc:
        # An exception leaving the scope is a sink too: it reaches callers, logs
        # and tracebacks after the scope has ended.
        safe = sanitize_exception(
            exc, functools.partial(redact_text, secrets=scope.secrets)
        )
        if safe is exc:
            raise
        raise safe from safe.__cause__
    finally:
        hooks.remove()
        _LOGGED.remove(scope)
        _ACTIVE.reset(token)


def add_active_secrets(values: cabc.Iterable[str]) -> None:
    """Hide ``values`` as well, until the outermost open scope ends.

    A command can bring secrets unknown when its scope opened, such as the
    tokens in its own Cargo home's credentials file. Every enclosing scope
    gains them, so messages built later hide them too. Outside any scope this
    does nothing, so a run that never activated secrets is unchanged.

    Examples
    --------
    >>> with use_secrets(("abc",)):
    ...     add_active_secrets(("xyz",))
    ...     redact_active("abc xyz")
    '<redacted> <redacted>'
    >>> redact_active("xyz")
    'xyz'
    """
    added = tuple(values)
    scope = _ACTIVE.get()
    while scope is not None:
        # Replaced whole, so a concurrent reader sees the old or the new set.
        scope.secrets = ordered_secrets((*scope.secrets, *added))
        scope = scope.parent


def _redact_logged(text: str) -> str:
    """Redact ``text`` with the secrets of every open scope, in one pass.

    Each scope's log hooks wrap the previous scope's, so the outermost runs
    first. Were it to use only its own secrets, it could replace the prefix of a
    longer secret an inner scope added and leave the rest showing. Every hook
    uses this one combined set instead: the first rewrite hides each secret
    whole, longest first, and the later hooks find nothing left to replace.

    Returns
    -------
    str
        ``text`` with every open scope's secrets replaced.
    """
    scopes = tuple(_LOGGED)
    return redact_text(
        text, tuple(itertools.chain.from_iterable(s.secrets for s in scopes))
    )


def _active_secrets() -> tuple[str, ...]:
    """Return the innermost open scope's secrets, or none outside any scope."""
    scope = _ACTIVE.get()
    return () if scope is None else scope.secrets


def redact_active(text: str) -> str:
    """Return ``text`` with the currently active secrets replaced.

    Returns
    -------
    str
        ``text`` unchanged when no secrets are active.
    """
    return redact_text(text, _active_secrets())


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
        self._plan = _plan(self._secrets)
        longest = max((len(secret) for secret in ordered_secrets(secrets)), default=0)
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
        if self._plan is None:
            self._pending = ""
            return buffer
        limit = len(buffer) - self._hold
        shown: list[str] = []
        consumed = 0
        for match in self._plan.pattern.finditer(buffer):
            if match.start() >= limit:
                break
            shown += [buffer[consumed : match.start()], self._plan.marker]
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
