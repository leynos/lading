"""Compiletest stderr artefact discovery and diagnostics formatting."""

from __future__ import annotations

import re
from pathlib import Path

_STDERR_PATTERN = re.compile(r"(/[^\s)]+\.stderr)")


def _trim_artefact_token(token: str) -> str:
    """Normalize compiletest artefact tokens by stripping punctuation."""
    return token.rstrip(")]:,.;'\"")


def _discover_stderr_artefacts(stream: str) -> tuple[Path, ...]:
    """Return ``Path`` objects extracted from compiletest output stream."""
    artefacts: list[Path] = []
    seen: set[str] = set()
    for match in _STDERR_PATTERN.finditer(stream):
        raw = _trim_artefact_token(match.group(1))
        if raw in seen:
            continue
        seen.add(raw)
        artefacts.append(Path(raw))
    return tuple(artefacts)


def _read_tail_lines(path: Path, count: int) -> tuple[str, ...]:
    """Return the last ``count`` lines from ``path`` when available."""
    if count <= 0:
        return ()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    lines = text.splitlines()
    return tuple(lines[-count:]) if lines else ()


def _format_artefact_diagnostics(artefact: Path, tail_lines: int) -> list[str]:
    """Return formatted diagnostic lines for a compiletest stderr artefact."""
    lines = [f"- {artefact}"]
    if not artefact.exists():
        lines.append("  (file not found)")
        return lines
    tail = _read_tail_lines(artefact, tail_lines)
    if not tail:
        return lines
    header = f"  Last {tail_lines} line(s):"
    lines.append(header)
    lines.extend(f"    {entry}" for entry in tail)
    return lines


def _append_compiletest_diagnostics(
    message: str,
    stdout: str,
    stderr: str,
    *,
    tail_lines: int,
) -> str:
    """Append compiletest stderr artefact hints to ``message`` when present."""
    artefacts: list[Path] = []
    seen: set[Path] = set()
    for candidate in (
        *_discover_stderr_artefacts(stdout),
        *_discover_stderr_artefacts(stderr),
    ):
        if candidate in seen:
            continue
        seen.add(candidate)
        artefacts.append(candidate)
    if not artefacts:
        return message
    lines = [message, "Compiletest stderr artefacts:"]
    for artefact in artefacts:
        lines.extend(_format_artefact_diagnostics(artefact, tail_lines))
    return "\n".join(lines)


__all__ = [
    "_append_compiletest_diagnostics",
]
