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

from tests.helpers.markdown_fence_scan import (
    MAX_FENCE_INDENT,
    closes_fence,
    dedent,
    fence_delimiter,
    indentation,
    list_context,
    list_item_end,
    quote_depth,
    strip_quotes,
    unmarked_fence_delimiter,
    unmarked_fence_end,
)

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


def load_tested_examples(
    path: Path, *, require_markers: bool = True
) -> dict[str, MarkedExample]:
    """Return every marked example in ``path`` keyed by identifier.

    Parameters
    ----------
    path : Path
        Markdown document to scan.
    require_markers : bool, optional
        When ``True`` (the default), a fence without a marker is an error.
        When ``False``, unmarked fences are skipped so a document can adopt
        the convention one example at a time without rewriting every fence.

    Returns
    -------
    dict[str, MarkedExample]
        Marked examples keyed by their identifier.

    Raises
    ------
    ValueError
        If ``require_markers`` is true and a fence has no marker, a marker is
        malformed or not followed by a fence, a fence is unterminated, or an
        identifier is duplicated.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    state = _LoadState(
        lines=lines,
        path=path,
        require_markers=require_markers,
        examples={},
    )
    index = 0
    while index < len(lines):
        index = _load_line(state, index)
    return state.examples


@dc.dataclass(slots=True)
class _LoadState:
    """Mutable state for one loader pass."""

    lines: cabc.Sequence[str]
    path: Path
    require_markers: bool
    examples: dict[str, MarkedExample]
    list_levels: list[int] = dc.field(default_factory=list)


def _load_line(state: _LoadState, index: int) -> int:
    """Load one line, returning the next index to inspect."""
    raw = state.lines[index]
    list_indent = list_context(raw, state.list_levels)
    marker = _marker_at(raw, state.path, index, list_indent)
    if marker is None:
        return _skip_line(state, index, list_indent)
    if marker.identifier in state.examples:
        message = (
            f"{state.path}:{index + 1}: duplicate tested-example {marker.identifier!r}"
        )
        raise ValueError(message)
    example, next_index = _read_marked_example(state.lines, index, marker)
    state.examples[marker.identifier] = example
    return next_index


def _skip_line(state: _LoadState, index: int, list_indent: int) -> int:
    """Return the next index after a line that carries no marker."""
    if state.require_markers:
        _reject_unmarked_fence(state.lines[index], state.path, index, list_indent)
        return index + 1
    # Skip the whole fence so a literal marker in its body is not read as an
    # example.
    skip_to = unmarked_fence_end(state.lines, index, list_indent)
    return skip_to if skip_to is not None else index + 1


def _reject_unmarked_fence(
    raw: str,
    path: Path,
    index: int,
    list_indent: int,
) -> None:
    """Raise when ``raw`` opens a fence that no marker introduces.

    Raises
    ------
    ValueError
        If the line opens a fence that no marker introduces.
    """
    if unmarked_fence_delimiter(raw, list_indent) is not None:
        message = f"{path}:{index + 1}: unmarked fenced example"
        raise ValueError(message)


def _marker_at(
    raw: str,
    path: Path,
    index: int,
    list_indent: int,
) -> _MarkerContext | None:
    """Return the marker on ``raw``, or ``None`` when it carries none."""
    depth = quote_depth(raw)
    content = strip_quotes(raw, depth) or ""
    item_end = list_item_end(content)
    marker_text = (content[item_end:] if item_end is not None else content).strip()
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
    opener = strip_quotes(lines[fence_start], marker.depth)
    if opener is None:  # pragma: no cover - _next_fence_line found it
        message = f"{marker.path}:{fence_start + 1}: malformed fenced example"
        raise ValueError(message)
    fence = opener.strip()
    delimiter = fence_delimiter(fence)
    if delimiter is None:  # pragma: no cover - _next_fence_line found it
        message = f"{marker.path}:{fence_start + 1}: malformed fenced example"
        raise ValueError(message)
    character, length = delimiter
    marked_fence = _MarkedFence(
        path=marker.path,
        identifier=marker.identifier,
        character=character,
        length=length,
        indent=indentation(opener),
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
        content = strip_quotes(lines[index], marker.depth)
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
    if fence_delimiter(content.strip()) is None:
        return False
    return indentation(content) <= marker.container_indent + MAX_FENCE_INDENT


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
        content = strip_quotes(lines[index], fence.depth)
        if content is None:
            break
        if _ends_list_container(fence, content):
            # An outdented line ends the list container, and with it the fence.
            break
        if closes_fence(
            content,
            fence.character,
            fence.length,
            fence.container_indent,
        ):
            return body, index
        body.append(dedent(content, fence.indent))
    message = (
        f"{fence.path}:{start}: tested-example {fence.identifier!r} is unterminated"
    )
    raise ValueError(message)


def _ends_list_container(fence: _MarkedFence, content: str) -> bool:
    """Return whether ``content`` is outdented from ``fence``'s list container."""
    if not content.strip() or not fence.container_indent:
        return False
    return indentation(content) < fence.container_indent
