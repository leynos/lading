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
        if stripped.startswith("```"):
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
        body, fence_end = _read_fence_body(lines, fence_start + 1, path, identifier)
        examples[identifier] = MarkedExample(
            identifier=identifier,
            language=fence[3:].strip(),
            body="\n".join(body),
            line=index + 1,
        )
        index = fence_end + 1
    return examples


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
        if stripped.startswith("```"):
            return index
        break
    message = f"{path}:{start}: tested-example {identifier!r} has no fence"
    raise ValueError(message)


def _read_fence_body(
    lines: cabc.Sequence[str],
    start: int,
    path: Path,
    identifier: str,
) -> tuple[list[str], int]:
    """Return the fence body and the index of its closing fence."""
    body: list[str] = []
    for index in range(start, len(lines)):
        if lines[index].strip() == "```":
            return body, index
        body.append(lines[index])
    message = f"{path}:{start}: tested-example {identifier!r} is unterminated"
    raise ValueError(message)
