"""Load documented examples that carry a stable ``tested-example`` marker.

A marker is an HTML comment of the form ``<!-- tested-example: <id> -->``
placed before a fenced block. The loader returns the fence body keyed by its
identifier and fails on a duplicate identifier, an unterminated fence, or a
marker that is not followed by a fence.
"""

import collections.abc as cabc
import dataclasses as dc
import re
import typing as typ
from pathlib import Path

__all__ = ["MarkedExample", "load_tested_examples"]

_MARKER: typ.Final[re.Pattern[str]] = re.compile(
    r"^<!--\s*tested-example:\s*([a-z0-9-]+)\s*-->$"
)
_MIN_FENCE_LENGTH: typ.Final[int] = 3
_BLOCK_QUOTE: typ.Final[re.Pattern[str]] = re.compile(r"^(?:>\s?)+")


@dc.dataclass(frozen=True, slots=True)
class MarkedExample:
    """One marked fenced example.

    Attributes
    ----------
    identifier : str
        Stable identifier from the marker.
    language : str
        Fence info string, such as ``bash``.
    body : str
        Fence body without the fence lines.
    line : int
        1-based line number of the marker.
    """

    identifier: str
    language: str
    body: str
    line: int


def load_tested_examples(path: Path) -> dict[str, MarkedExample]:
    """Return every marked example in ``path`` keyed by identifier.

    Parameters
    ----------
    path : Path
        Markdown document to scan.

    Returns
    -------
    dict[str, MarkedExample]
        Marked examples keyed by their identifier.

    Raises
    ------
    ValueError
        If a fence has no marker, a marker is malformed or not followed by a
        fence, a fence is unterminated, or an identifier is duplicated.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    examples: dict[str, MarkedExample] = {}
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if _fence_delimiter(_without_block_quote(stripped)) is not None:
            message = f"{path}:{index + 1}: unmarked fenced example"
            raise ValueError(message)
        if stripped.startswith("<!-- tested-example:"):
            match = _MARKER.match(stripped)
            if match is None:
                message = f"{path}:{index + 1}: malformed tested-example marker"
                raise ValueError(message)
        else:
            index += 1
            continue
        identifier = match.group(1)
        if identifier in examples:
            message = f"{path}:{index + 1}: duplicate tested-example {identifier!r}"
            raise ValueError(message)
        fence_start = _next_fence_line(lines, index + 1, path, identifier)
        fence = lines[fence_start].strip()
        delimiter = _fence_delimiter(fence)
        if delimiter is None:  # pragma: no cover - _next_fence_line found it
            message = f"{path}:{fence_start + 1}: malformed fenced example"
            raise ValueError(message)
        character, length = delimiter
        marked_fence = _MarkedFence(
            path=path,
            identifier=identifier,
            character=character,
            length=length,
        )
        body, fence_end = _read_fence_body(lines, fence_start + 1, marked_fence)
        examples[identifier] = MarkedExample(
            identifier=identifier,
            language=fence[length:].strip(),
            body="\n".join(body),
            line=index + 1,
        )
        index = fence_end + 1
    return examples


def _without_block_quote(line: str) -> str:
    """Return ``line`` without any leading block-quote markers."""
    return _BLOCK_QUOTE.sub("", line).strip()


def _fence_delimiter(line: str) -> tuple[str, int] | None:
    """Return the fence character and run length for an opening fence."""
    for character in ("`", "~"):
        length = len(line) - len(line.lstrip(character))
        if length >= _MIN_FENCE_LENGTH and line.startswith(character * length):
            return character, length
    return None


def _next_fence_line(
    lines: cabc.Sequence[str],
    start: int,
    path: Path,
    identifier: str,
) -> int:
    """Return the line index of the fence following a marker."""
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if _fence_delimiter(stripped) is not None:
            return index
        break
    message = f"{path}:{start}: tested-example {identifier!r} has no fence"
    raise ValueError(message)


@dc.dataclass(frozen=True, slots=True)
class _MarkedFence:
    """One opened fence and the diagnostic context that named it."""

    path: Path
    identifier: str
    character: str
    length: int


def _read_fence_body(
    lines: cabc.Sequence[str],
    start: int,
    fence: _MarkedFence,
) -> tuple[list[str], int]:
    """Return the fence body and the index of its matching closing fence."""
    body: list[str] = []
    for index in range(start, len(lines)):
        if _closes_fence(lines[index].strip(), fence.character, fence.length):
            return body, index
        body.append(lines[index])
    message = (
        f"{fence.path}:{start}: tested-example {fence.identifier!r} is unterminated"
    )
    raise ValueError(message)


def _closes_fence(line: str, character: str, length: int) -> bool:
    """Return whether ``line`` closes a fence of ``character`` and ``length``."""
    run = len(line) - len(line.lstrip(character))
    return run >= length and line == character * run
