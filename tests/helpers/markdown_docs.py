"""Helpers for writing small Markdown documents in tests."""

from pathlib import Path

__all__ = ["write_document"]


def write_document(directory: Path, text: str) -> Path:
    """Write ``text`` to ``doc.md`` in ``directory`` and return its path."""
    path = directory / "doc.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
