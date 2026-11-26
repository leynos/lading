"""Keep known registry credentials out of relayed and captured subprocess output.

A build script, a test, or a Cargo diagnostic can print the value of
``CARGO_REGISTRY_TOKEN`` that lading's environment holds. Whatever a subprocess
prints is mirrored to lading's own streams and embedded in the errors and logs
built from the captured text, so the one place to stop a credential is where
the output is read.

The redaction is deliberately narrow: it replaces the exact values of the
registry token variables lading was given, never a pattern, so it cannot
mangle unrelated output. A value can straddle two reads from a pipe, so
:class:`SecretRedactor` holds back the final ``len(longest) - 1`` characters
until the next read proves they do not begin a secret.

Examples
--------
>>> import io
>>> reader = RedactingReader(io.BytesIO(b"password hunter2 done"), ("hunter2",))
>>> b"".join(iter(lambda: reader.read(8), b""))
b'password <redacted> done'
>>> registry_token_values({"CARGO_REGISTRY_TOKEN": "abc", "HOME": "/root"})
('abc',)
"""

import collections.abc as cabc
import re
import typing as typ

REDACTED = "<redacted>"
_REDACTED_BYTES = REDACTED.encode("ascii")

# Cargo's two credential variables: the crates.io token and a named registry's.
_TOKEN_VARIABLE = re.compile(r"CARGO_REGISTRY_TOKEN|CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")


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
    values = {
        str(value)
        for key, value in environ.items()
        if _TOKEN_VARIABLE.fullmatch(key) and str(value)
    }
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


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


class RedactingReader:
    """A binary reader that replaces exact secret values as it reads.

    It wraps a pipe so that everything downstream, whether mirrored or
    captured, is already clean. Replacement works on bytes, so it is
    independent of how the text is later decoded. Holding back a short tail
    means a value split across two reads is still found; the tail is released
    at end of input.

    Parameters
    ----------
    source : typ.IO[bytes]
        The stream to wrap.
    secrets : cabc.Sequence[str]
        The values to replace, longest first.
    """

    def __init__(self, source: typ.IO[bytes], secrets: cabc.Sequence[str]) -> None:
        """Remember ``source`` and the encoded secrets; hold nothing back yet."""
        self._source = source
        # Closing is the wrapped pipe's business; relay_stream calls it in `finally`.
        self.close = source.close
        self._secrets = tuple(secret.encode("utf-8") for secret in secrets)
        self._hold = max((len(secret) for secret in self._secrets), default=1) - 1
        self._pending = b""

    def _replace(self, data: bytes) -> bytes:
        """Return ``data`` with every secret replaced."""
        for secret in self._secrets:
            data = data.replace(secret, _REDACTED_BYTES)
        return data

    def read(self, size: int = -1) -> bytes:
        """Return the next safe bytes, or ``b""`` once the stream has ended.

        Parameters
        ----------
        size : int
            How much to read from the wrapped stream at a time.

        Returns
        -------
        bytes
            Redacted bytes. Possibly more than ``size`` when a secret is
            replaced by a longer placeholder.
        """
        while True:
            chunk = self._source.read(size)
            if not chunk:
                remainder, self._pending = self._replace(self._pending), b""
                return remainder
            combined = self._replace(self._pending + chunk)
            keep = min(self._hold, len(combined))
            self._pending = combined[len(combined) - keep :]
            emit = combined[: len(combined) - keep]
            if emit:
                return emit


def guard_stream(
    source: typ.IO[bytes] | None, secrets: cabc.Sequence[str]
) -> typ.IO[bytes] | None:
    """Wrap ``source`` so ``secrets`` never come out of it.

    Returns
    -------
    typ.IO[bytes] | None
        ``source`` itself when there is nothing to hide, so the common case
        adds no work; otherwise a :class:`RedactingReader` over it.
    """
    if source is None or not secrets:
        return source
    return typ.cast("typ.IO[bytes]", RedactingReader(source, secrets))
