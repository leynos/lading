r"""Carry the secrets a digest-checked run hides, and hide them where output leaves.

:func:`use_secrets` opens a scope for a block of work and yields it as a
:class:`SecretScope`. That object is the explicit handle given to anything that
learns secrets mid-command, such as the redacting runner reading a command's
own Cargo home, so it can hide them as well. A command never reaches for an
enclosing scope: what a scope learned is handed back to the scope that encloses
it as the block ends, so a token one command revealed stays hidden from later
diagnostics until the outermost scope ends.

While any scope is open the log hooks of :mod:`lading.utils.log_redaction` are
installed, so a record built without redaction is still hidden where it leaves
lading, and an exception leaving a scope is sanitized on its way out.
:func:`redact_active` serves the remaining sinks, such as the command line's
error printer.

Examples
--------
>>> with use_secrets(("abc",)) as scope:
...     scope.hide(("xyz",))
...     redact_active("abc xyz")
'<redacted> <redacted>'
>>> redact_active("xyz")
'xyz'
"""

import collections.abc as cabc
import contextlib
import contextvars
import itertools

from lading.utils.exception_redaction import sanitize_exception
from lading.utils.log_redaction import LogRedaction
from lading.utils.redaction import ordered_secrets, redact_text


class SecretScope:
    """The secrets one block hides, which a command it is given may extend.

    Parameters
    ----------
    secrets : cabc.Iterable[str]
        The values known when the scope opens.
    parent : SecretScope | None
        The enclosing scope, whose secrets this one hides as well.

    Examples
    --------
    >>> scope = SecretScope(("abc",))
    >>> scope.hide(("xyz",))
    >>> scope.redact("abc xyz")
    '<redacted> <redacted>'
    """

    __slots__ = ("_own", "parent")

    def __init__(
        self, secrets: cabc.Iterable[str] = (), *, parent: SecretScope | None = None
    ) -> None:
        """Hold ``secrets`` beneath ``parent``."""
        self._own = ordered_secrets(secrets)
        self.parent = parent

    @property
    def own(self) -> tuple[str, ...]:
        """The values this scope was opened with or has learned since."""
        return self._own

    @property
    def secrets(self) -> tuple[str, ...]:
        """Every value hidden here: this scope's and each enclosing one's."""
        chain = itertools.chain.from_iterable(scope.own for scope in self._lineage())
        return ordered_secrets(chain)

    def _lineage(self) -> cabc.Iterator[SecretScope]:
        """Yield this scope, then each enclosing scope outwards."""
        scope: SecretScope | None = self
        while scope is not None:
            yield scope
            scope = scope.parent

    def hide(self, values: cabc.Iterable[str]) -> None:
        """Hide ``values`` too, for the rest of this scope.

        The tuple is replaced whole, so a concurrent reader sees either the
        old or the new set.
        """
        self._own = ordered_secrets((*self._own, *values))

    def redact(self, text: str) -> str:
        """Return ``text`` with every secret this scope hides replaced.

        Returns
        -------
        str
            ``text`` with each known value replaced by the marker.
        """
        return redact_text(text, self.secrets)


# The innermost open scope in this context.
_ACTIVE: contextvars.ContextVar[SecretScope | None] = contextvars.ContextVar(
    "lading_active_secrets", default=None
)
# Every open scope. The log hooks are process-wide, so they read this rather
# than the per-context ``_ACTIVE``.
_LOGGED: list[SecretScope] = []


@contextlib.contextmanager
def use_secrets(secrets: cabc.Iterable[str] = ()) -> cabc.Generator[SecretScope]:
    """Hide ``secrets`` (in addition to any already active) from shown output.

    For the duration of the block the secrets are replaced by
    :func:`redact_active` and by log hooks installed for every logger and
    handler (see :mod:`lading.utils.log_redaction`). The hooks are removed
    afterwards, so nothing changes for a run that never opens a scope. They
    redact with the secrets of every open scope at once (see
    :func:`_redact_logged`). An exception that propagates out of the block is
    sanitized first (see
    :func:`~lading.utils.exception_redaction.sanitize_exception`).

    When the block ends, what the scope holds is handed to the scope that
    encloses it, so a value learned inside stays hidden until the outermost
    scope ends.

    Yields
    ------
    SecretScope
        The open scope, to give to whatever may learn further secrets.
    """
    scope = SecretScope(secrets, parent=_ACTIVE.get())
    token = _ACTIVE.set(scope)
    _LOGGED.append(scope)
    hooks = LogRedaction(_redact_logged)
    try:
        yield scope
    except Exception as exc:
        # An exception leaving the scope is a sink too: it reaches callers, logs
        # and tracebacks after the scope has ended.
        safe = sanitize_exception(exc, scope.redact)
        if safe is exc:
            raise
        raise safe from safe.__cause__
    finally:
        hooks.remove()
        _LOGGED.remove(scope)
        _ACTIVE.reset(token)
        if scope.parent is not None:
            scope.parent.hide(scope.own)


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


def redact_active(text: str) -> str:
    """Return ``text`` with the currently active secrets replaced.

    Returns
    -------
    str
        ``text`` unchanged when no scope is open in this context.
    """
    scope = _ACTIVE.get()
    return text if scope is None else scope.redact(text)
