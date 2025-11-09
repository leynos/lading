"""Compiletest stderr artifact discovery and diagnostics formatting."""

from __future__ import annotations

import re
from pathlib import Path

_STDERR_PATTERN = re.compile(r"(/[^\s)]+\\.stderr)")


def _trim_artifact_token(token: str) -> str:
    """Normalise compiletest artifact tokens by stripping punctuation."""
    return token.rstrip(")]:,.;'\"")


def _discover_stderr_artifacts(stream: str) -> tuple[Path, ...]:
    """Return ``Path`` objects extracted from compiletest output stream."""
    artifacts: list[Path] = []
    seen: set[str] = set()
    for match in _STDERR_PATTERN.finditer(stream):
        raw = _trim_artifact_token(match.group(1))
        if raw in seen:
            continue
        seen.add(raw)
        artifacts.append(Path(raw))
    return tuple(artifacts)


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


def _format_artifact_diagnostics(artifact: Path, tail_lines: int) -> list[str]:
    """Return formatted diagnostic lines for a compiletest stderr artifact."""
    lines = [f"- {artifact}"]
    if not artifact.exists():
        lines.append("  (file not found)")
        return lines
    tail = _read_tail_lines(artifact, tail_lines)
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
    """Append compiletest stderr artifact hints to ``message`` when present."""
    artifacts: list[Path] = []
    seen: set[Path] = set()
    for candidate in (
        *_discover_stderr_artifacts(stdout),
        *_discover_stderr_artifacts(stderr),
    ):
        if candidate in seen:
            continue
        seen.add(candidate)
        artifacts.append(candidate)
    if not artifacts:
        return message
    lines = [message, "Compiletest stderr artifacts:"]
    for artifact in artifacts:
        lines.extend(_format_artifact_diagnostics(artifact, tail_lines))
    return "\n".join(lines)


__all__ = [
    "_append_compiletest_diagnostics",
]
