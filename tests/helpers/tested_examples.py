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
_MAX_FENCE_INDENT: typ.Final[int] = 3
_CONTAINER_MARKERS: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:>\s?)|(?:[-+*]|\d{1,9}[.)])\s+)+"
)
_QUOTE_TOKEN: typ.Final[re.Pattern[str]] = re.compile(r"^ {0,3}> ?")
_LIST_ITEM: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?P<indent> *)(?:[-+*]|\d{1,9}[.)])(?P<space>[ \t]+)"
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
    list_levels: list[int] = []
    while index < len(lines):
        raw = lines[index]
        list_indent = _list_context(raw, list_levels)
        _reject_unmarked_fence(raw, path, index, list_indent)
        marker = _marker_at(raw, path, index, list_indent)
        if marker is None:
            index += 1
            continue
        if marker.identifier in examples:
            message = (
                f"{path}:{index + 1}: duplicate tested-example {marker.identifier!r}"
            )
            raise ValueError(message)
        example, next_index = _read_marked_example(lines, index, marker)
        examples[marker.identifier] = example
        index = next_index
    return examples


def _list_context(raw: str, levels: list[int]) -> int:
    """Return the content indentation of the enclosing list item, if any.

    ``levels`` holds the content indentation of each open list level. An
    outdented line closes deeper levels, restoring the matching ancestor.

    Returns
    -------
    int
        The content indentation of the enclosing list item, or ``0`` outside a
        list.
    """
    depth = _quote_depth(raw)
    content = _strip_quotes(raw, depth) or ""
    match = _LIST_ITEM.match(content)
    if match is not None:
        indent = match.end()
        while levels and levels[-1] >= indent:
            levels.pop()
        levels.append(indent)
        return indent
    if content.strip():
        indentation = _indentation(content)
        while levels and levels[-1] > indentation:
            levels.pop()
    return levels[-1] if levels else 0


def _reject_unmarked_fence(
    raw: str,
    path: Path,
    index: int,
    list_indent: int,
) -> None:
    """Raise when ``raw`` opens a fence that no marker introduces.

    A delimiter indented beyond its container's limit is literal code, not a
    fence, so it is left alone.

    Raises
    ------
    ValueError
        If the line opens a fence that no marker introduces.
    """
    depth = _quote_depth(raw)
    content = _strip_quotes(raw, depth) or ""
    list_item = _LIST_ITEM.match(content)
    if list_item is not None:
        base = list_item.end()
        remainder = content[base:]
    else:
        base = list_indent
        remainder = content
    if _indentation(remainder) > base + _MAX_FENCE_INDENT:
        return
    if _fence_delimiter(remainder.strip()) is not None:
        message = f"{path}:{index + 1}: unmarked fenced example"
        raise ValueError(message)


def _marker_at(
    raw: str,
    path: Path,
    index: int,
    list_indent: int,
) -> _MarkerContext | None:
    """Return the marker on ``raw``, or ``None`` when it carries none."""
    depth = _quote_depth(raw)
    content = _strip_quotes(raw, depth) or ""
    list_item = _LIST_ITEM.match(content)
    marker_text = (
        content[list_item.end() :] if list_item is not None else content
    ).strip()
    container_indent = list_indent
    if not marker_text.startswith("<!-- tested-example:"):
        return None
    match = _MARKER.match(marker_text)
    if match is None:
        message = f"{path}:{index + 1}: malformed tested-example marker"
        raise ValueError(message)
    return _MarkerContext(
        path=path,
        identifier=match.group(1),
        depth=depth,
        container_indent=container_indent,
    )


def _read_marked_example(
    lines: cabc.Sequence[str],
    index: int,
    marker: _MarkerContext,
) -> tuple[MarkedExample, int]:
    """Read the fence that follows a marker and return it with the next index."""
    fence_start = _next_fence_line(lines, index + 1, marker)
    opener = _strip_quotes(lines[fence_start], marker.depth)
    if opener is None:  # pragma: no cover - _next_fence_line found it
        message = f"{marker.path}:{fence_start + 1}: malformed fenced example"
        raise ValueError(message)
    fence = opener.strip()
    delimiter = _fence_delimiter(fence)
    if delimiter is None:  # pragma: no cover - _next_fence_line found it
        message = f"{marker.path}:{fence_start + 1}: malformed fenced example"
        raise ValueError(message)
    character, length = delimiter
    marked_fence = _MarkedFence(
        path=marker.path,
        identifier=marker.identifier,
        character=character,
        length=length,
        indent=_indentation(opener),
        depth=marker.depth,
        container_indent=marker.container_indent,
    )
    body, fence_end = _read_fence_body(lines, fence_start + 1, marked_fence)
    example = MarkedExample(
        identifier=marker.identifier,
        language=fence[length:].strip(),
        body="\n".join(body),
        line=index + 1,
    )
    return example, fence_end + 1


def _without_container(line: str) -> str:
    """Return ``line`` without leading block-quote or list-item markers."""
    return _CONTAINER_MARKERS.sub("", line).strip()


def _quote_depth(line: str) -> int:
    """Return the number of block-quote markers that prefix ``line``."""
    depth = 0
    remainder = line
    while (match := _QUOTE_TOKEN.match(remainder)) is not None:
        remainder = remainder[match.end() :]
        depth += 1
    return depth


def _strip_quotes(line: str, depth: int) -> str | None:
    """Remove ``depth`` block-quote markers, or ``None`` when the quote ends."""
    remainder = line
    for _ in range(depth):
        match = _QUOTE_TOKEN.match(remainder)
        if match is None:
            return None
        remainder = remainder[match.end() :]
    return remainder


def _fence_delimiter(line: str) -> tuple[str, int] | None:
    """Return the fence character and run length for an opening fence."""
    for character in ("`", "~"):
        length = len(line) - len(line.lstrip(character))
        if length >= _MIN_FENCE_LENGTH and line.startswith(character * length):
            return character, length
    return None


@dc.dataclass(frozen=True, slots=True)
class _MarkerContext:
    """The marker that opened an example and its container context."""

    path: Path
    identifier: str
    depth: int
    container_indent: int


def _next_fence_line(
    lines: cabc.Sequence[str],
    start: int,
    marker: _MarkerContext,
) -> int:
    """Return the line index of the fence following a marker."""
    for index in range(start, len(lines)):
        content = _strip_quotes(lines[index], marker.depth)
        if content is None:
            break
        if not content.strip():
            continue
        if _opens_fence(content, marker):
            return index
        break
    message = (
        f"{marker.path}:{start}: tested-example {marker.identifier!r} has no fence"
    )
    raise ValueError(message)


def _opens_fence(content: str, marker: _MarkerContext) -> bool:
    """Return whether ``content`` opens a fence the marker can use.

    Returns
    -------
    bool
        ``True`` for a fence delimiter indented no further than the marker's
        container allows.
    """
    if _fence_delimiter(content.strip()) is None:
        return False
    return _indentation(content) <= marker.container_indent + _MAX_FENCE_INDENT


@dc.dataclass(frozen=True, slots=True)
class _MarkedFence:
    """One opened fence and the diagnostic context that named it."""

    path: Path
    identifier: str
    character: str
    length: int
    indent: int
    depth: int
    container_indent: int


def _read_fence_body(
    lines: cabc.Sequence[str],
    start: int,
    fence: _MarkedFence,
) -> tuple[list[str], int]:
    """Return the fence body and the index of its matching closing fence."""
    body: list[str] = []
    for index in range(start, len(lines)):
        content = _strip_quotes(lines[index], fence.depth)
        if content is None:
            break
        if _ends_list_container(fence, content):
            # An outdented line ends the list container, and with it the fence.
            break
        if _closes_fence(
            content,
            fence.character,
            fence.length,
            fence.container_indent,
        ):
            return body, index
        body.append(_dedent(content, fence.indent))
    message = (
        f"{fence.path}:{start}: tested-example {fence.identifier!r} is unterminated"
    )
    raise ValueError(message)


def _ends_list_container(fence: _MarkedFence, content: str) -> bool:
    """Return whether ``content`` is outdented from ``fence``'s list container."""
    if not content.strip() or not fence.container_indent:
        return False
    return _indentation(content) < fence.container_indent


def _closes_fence(
    line: str,
    character: str,
    length: int,
    container_indent: int,
) -> bool:
    """Return whether ``line`` closes a fence of ``character`` and ``length``.

    GitHub-flavoured Markdown permits at most three leading spaces relative to
    the container. A list item's marker sets the container indentation; a
    top-level fence has none, so its opener's own indentation does not extend
    the limit.

    Returns
    -------
    bool
        Whether ``line`` closes the fence.
    """
    allowed = container_indent + _MAX_FENCE_INDENT
    if _indentation(line) > allowed:
        return False
    stripped = line.strip()
    run = len(stripped) - len(stripped.lstrip(character))
    return run >= length and stripped == character * run


def _indentation(line: str) -> int:
    """Return the number of leading spaces before any other character."""
    return len(line) - len(line.lstrip(" "))


def _dedent(line: str, indent: int) -> str:
    """Remove up to ``indent`` leading spaces from ``line``."""
    removed = 0
    while removed < indent and line.startswith(" "):
        line = line[1:]
        removed += 1
    return line
