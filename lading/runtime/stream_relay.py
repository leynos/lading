"""Stateful subprocess stream mirroring helpers."""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

_THREAD_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def format_thread_name(program: str, stream: str) -> str:
    """Return a deterministic, filesystem-safe thread name suffix."""
    base = Path(program).name or program
    safe = _THREAD_NAME_PATTERN.sub("-", base).strip("-") or "command"
    return f"lading-cmd-{safe}-{stream}"


def write_to_relay_sink(
    sink: typ.TextIO | None,
    binary_sink: typ.BinaryIO | None,
    payload: str,
) -> tuple[typ.TextIO | None, typ.BinaryIO | None]:
    """Mirror one relay payload, retaining a selected binary buffer."""
    if sink is None or not payload:
        return sink, binary_sink
    if binary_sink is not None:
        return _write_to_binary_sink(sink, binary_sink, payload)
    return _write_to_text_sink(sink, payload)


def _write_to_binary_sink(
    sink: typ.TextIO,
    binary_sink: typ.BinaryIO,
    payload: str,
) -> tuple[typ.TextIO | None, typ.BinaryIO | None]:
    """Write a relay payload through an already-selected binary buffer."""
    try:
        binary_sink.write(payload.encode("utf-8"))
        binary_sink.flush()
    except BrokenPipeError:
        return None, None
    return sink, binary_sink


def _write_to_text_sink(
    sink: typ.TextIO,
    payload: str,
) -> tuple[typ.TextIO | None, typ.BinaryIO | None]:
    """Write text and select the binary buffer after an encoding failure."""
    try:
        sink.write(payload)
        sink.flush()
    except BrokenPipeError:
        return None, None
    except UnicodeEncodeError:
        selected_binary_sink = typ.cast(
            "typ.BinaryIO | None", getattr(sink, "buffer", None)
        )
        if selected_binary_sink is None:
            return None, None
        try:
            sink.flush()
        except BrokenPipeError:
            return None, None
        return _write_to_binary_sink(sink, selected_binary_sink, payload)
    return sink, None
