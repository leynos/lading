"""Mirror decoded subprocess output through text or binary parent streams.

The helpers keep subprocess capture independent of output mirroring when a
parent text stream cannot encode decoded UTF-8. Relay callers retain the sink
state returned by :func:`write_to_relay_sink` for each successive output chunk,
which preserves normal text writes until a Unicode encoding failure selects the
parent stream's UTF-8 binary buffer.

Typical use is to derive deterministic thread names with
:func:`format_thread_name`, then pass the active text and binary sinks through
:func:`write_to_relay_sink` while relaying a subprocess stream.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

from .relay_events import StreamName, emit_relay_event

_THREAD_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def format_thread_name(program: str, stream: StreamName) -> str:
    """Return a deterministic, filesystem-safe relay thread name.

    Parameters
    ----------
    program : str
        Program path or name associated with the subprocess stream.
    stream : StreamName
        ``"stdout"`` or ``"stderr"`` label for the child stream.

    Returns
    -------
    str
        A ``lading-cmd-`` prefixed name using the program basename and stream
        label, with other characters replaced by hyphens.

    Examples
    --------
    >>> format_thread_name("C:/Tools/cargo.exe", "stdout")
    'lading-cmd-cargo.exe-stdout'
    """
    base = Path(program).name or program
    safe = _THREAD_NAME_PATTERN.sub("-", base).strip("-") or "command"
    return f"lading-cmd-{safe}-{stream}"


def write_to_relay_sink(
    sink: typ.TextIO | None,
    binary_sink: typ.BinaryIO | None,
    stream: StreamName,
    payload: str,
) -> tuple[typ.TextIO | None, typ.BinaryIO | None]:
    """Mirror one decoded relay payload and return the next sink state.

    Parameters
    ----------
    sink : typ.TextIO | None
        Active parent text stream, or :data:`None` when mirroring is disabled.
    binary_sink : typ.BinaryIO | None
        Active parent binary stream after an earlier encoding fallback, or
        :data:`None` while text mirroring remains active.
    stream : StreamName
        ``"stdout"`` or ``"stderr"`` label for the relayed child stream.
    payload : str
        Decoded UTF-8 text to mirror.

    Returns
    -------
    tuple[typ.TextIO | None, typ.BinaryIO | None]
        The active text stream and persistent binary stream for the next relay
        payload. A Unicode encoding failure selects ``sink.buffer`` and writes
        this and later payloads as exact UTF-8 bytes. A text-only sink returns
        ``(None, None)`` to disable mirroring without affecting capture.

    Examples
    --------
    >>> import io
    >>> sink = io.StringIO()
    >>> active_sink, binary_sink = write_to_relay_sink(
    ...     sink, None, "stdout", "hello"
    ... )
    >>> active_sink is sink and binary_sink is None
    True
    """
    if sink is None or not payload:
        return sink, binary_sink
    if binary_sink is not None:
        return _write_to_binary_sink(sink, binary_sink, stream, payload)
    return _write_to_text_sink(sink, stream, payload)


def _write_to_binary_sink(
    sink: typ.TextIO,
    binary_sink: typ.BinaryIO,
    stream: StreamName,
    payload: str,
) -> tuple[typ.TextIO | None, typ.BinaryIO | None]:
    """Write a relay payload through an already-selected binary buffer."""
    try:
        binary_sink.write(payload.encode("utf-8"))
        binary_sink.flush()
    except BrokenPipeError:
        emit_relay_event("relay_mirror", stream, "disable_mirroring", "broken_pipe")
        return None, None
    return sink, binary_sink


def _write_to_text_sink(
    sink: typ.TextIO,
    stream: StreamName,
    payload: str,
) -> tuple[typ.TextIO | None, typ.BinaryIO | None]:
    """Write text and select the binary buffer after an encoding failure."""
    try:
        sink.write(payload)
        sink.flush()
    except BrokenPipeError:
        emit_relay_event("relay_mirror", stream, "disable_mirroring", "broken_pipe")
        return None, None
    except UnicodeEncodeError:
        selected_binary_sink = typ.cast(
            "typ.BinaryIO | None", getattr(sink, "buffer", None)
        )
        if selected_binary_sink is None:
            emit_relay_event(
                "relay_mirror", stream, "disable_mirroring", "unicode_encode"
            )
            return None, None
        try:
            sink.flush()
        except BrokenPipeError:
            emit_relay_event("relay_mirror", stream, "disable_mirroring", "broken_pipe")
            return None, None
        emit_relay_event("relay_mirror", stream, "text_to_binary", "unicode_encode")
        return _write_to_binary_sink(sink, selected_binary_sink, stream, payload)
    return sink, None
