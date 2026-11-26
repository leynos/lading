r"""Keep known registry credentials out of what lading shows.

A build script, a test, or a Cargo diagnostic can print the value of
``CARGO_REGISTRY_TOKEN`` that lading's environment holds. Redaction here
protects what lading *shows*: the console relay, log lines, and the error
messages built from captured output. It never changes what lading *parses*:
captured streams stay exactly as the child wrote them, so a value that happens
to coincide with, say, ``cargo metadata`` JSON cannot corrupt it.

There are two halves. :class:`SecretRedactor` filters a stream of text chunks,
for the relay. :func:`use_secrets` and :func:`redact_active` carry the known
values for the duration of a digest-checked run, so the small set of helpers
that embed captured output in messages (:func:`lading.utils.process.command_detail`
and :func:`~lading.utils.process.append_detail`) redact in one place rather than
at every call site.

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
import re

REDACTED = "<redacted>"

# Cargo's two credential variables: the crates.io token and a named registry's.
_TOKEN_VARIABLE = re.compile(r"CARGO_REGISTRY_TOKEN|CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")
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


@contextlib.contextmanager
def use_secrets(secrets: cabc.Iterable[str]) -> cabc.Generator[None]:
    """Hide ``secrets`` (in addition to any already active) from shown messages."""
    token = _ACTIVE.set(_longest_first((*_ACTIVE.get(), *secrets)))
    try:
        yield
    finally:
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
