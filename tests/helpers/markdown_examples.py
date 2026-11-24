"""Load marked fenced examples from published Markdown documents."""

import collections.abc as cabc
import dataclasses as dc
import re
import typing as typ
from pathlib import Path

from markdown_it import MarkdownIt

if typ.TYPE_CHECKING:
    from markdown_it.token import Token

_MARKER_PREFIX = "<!-- tested-example:"
_MARKER_PATTERN = re.compile(r"<!-- tested-example: ([a-z0-9][a-z0-9_-]*) -->")
_DIRECT_MARKER_GAP = 1
_BLANK_LINE_MARKER_GAP = 2
_MAX_MARKER_INDENT = 3


class MarkdownExampleError(ValueError):
    """Raised when a Markdown document has invalid tested-example fences."""

    def __init__(self, document_name: str, line_number: int, problem: str) -> None:
        """Format a validation problem with its document location.

        Parameters
        ----------
        document_name : str
            Path or label of the Markdown document being checked.
        line_number : int
            One-based line where the malformed marker or fence was found.
        problem : str
            Short description of the violated marker or fence rule.
        """
        super().__init__(f"{document_name}:{line_number}: {problem}")


@dc.dataclass(frozen=True, slots=True)
class MarkdownExample:
    """A marked fenced example extracted from a published document."""

    identifier: str
    language: str
    source: str


@dc.dataclass(frozen=True, slots=True)
class _ContainerPath:
    """Identify the list-item and blockquote ancestry of one token."""

    list_item_path: tuple[int, ...]
    blockquote_path: tuple[int, ...]


@dc.dataclass(frozen=True, slots=True)
class _IndexedMarker:
    """Identify a marker's source line and Markdown containers."""

    identifier: str
    level: int
    container_path: _ContainerPath


@dc.dataclass(frozen=True, slots=True)
class _MarkerLocation:
    """Carry source and container identity while parsing one marker line."""

    line_number: int
    token_level: int
    container_path: _ContainerPath
    document_name: str


@dc.dataclass(slots=True)
class _MarkerIndex:
    """Collect marker metadata while the token stream is traversed."""

    document_name: str
    markers: dict[int, _IndexedMarker] = dc.field(default_factory=dict)
    seen_identifiers: set[str] = dc.field(default_factory=set)


@dc.dataclass(frozen=True, slots=True)
class _MarkerContext:
    """Carry marker locations with the physical source needed to validate them."""

    markers: dict[int, _IndexedMarker]
    source_lines: tuple[str, ...]
    document_name: str
    container_paths: tuple[_ContainerPath, ...]


def load_markdown_examples(path: Path) -> tuple[MarkdownExample, ...]:
    """Load every marked fenced example from a Markdown document.

    Parameters
    ----------
    path : Path
        Published Markdown document whose examples are under test.

    Returns
    -------
    tuple[MarkdownExample, ...]
        Examples in document order, with stable identifiers and source text.

    Malformed or ambiguous fences raise ``MarkdownExampleError``. Read errors
    from ``Path.read_text`` propagate as ``OSError``.

    """
    return _parse_markdown_examples(path.read_text(encoding="utf-8"), str(path))


def _parse_markdown_examples(
    document: str, document_name: str
) -> tuple[MarkdownExample, ...]:
    """Parse CommonMark containers and enforce unique markers for every fence."""
    tokens = MarkdownIt("commonmark").parse(document)
    marker_context = _index_markers(tokens, tuple(document.split("\n")), document_name)
    examples: list[MarkdownExample] = []
    consumed_markers: set[int] = set()
    for token_index, token in enumerate(tokens):
        if token.type != "fence":
            continue
        start_line, end_line = _source_lines(token, document_name)
        _require_explicit_fence_close(token, start_line, end_line, document_name)
        marker_line, identifier = _require_example_marker(
            marker_context,
            start_line,
            token.level,
            marker_context.container_paths[token_index],
        )
        language = _require_language(token.info, start_line, document_name)
        examples.append(
            MarkdownExample(
                identifier=identifier,
                language=language,
                source=token.content.removesuffix("\n"),
            )
        )
        consumed_markers.add(marker_line)
    _check_markers_have_fences(marker_context.markers, document_name, consumed_markers)
    return tuple(examples)


def _index_markers(
    tokens: cabc.Sequence[Token],
    source_lines: tuple[str, ...],
    document_name: str,
) -> _MarkerContext:
    """Index valid marker comments and reject missing or duplicate identifiers."""
    marker_index = _MarkerIndex(document_name)
    list_item_stack: list[int] = []
    blockquote_stack: list[int] = []
    container_paths: list[_ContainerPath] = []
    for token_index, token in enumerate(tokens):
        if token.type == "blockquote_open":
            blockquote_stack.append(token_index)
        if token.type == "list_item_open":
            list_item_stack.append(token_index)
        container_path = _ContainerPath(tuple(list_item_stack), tuple(blockquote_stack))
        container_paths.append(container_path)
        _index_token_markers(token, marker_index, container_path)
        if token.type == "list_item_close":
            list_item_stack.pop()
        if token.type == "blockquote_close":
            blockquote_stack.pop()
    return _MarkerContext(
        marker_index.markers,
        source_lines,
        document_name,
        tuple(container_paths),
    )


def _index_token_markers(
    token: Token,
    marker_index: _MarkerIndex,
    container_path: _ContainerPath,
) -> None:
    """Index valid marker comments from one HTML block token."""
    if token.type != "html_block" or token.map is None:
        return
    start_line = token.map[0]
    for offset, line in enumerate(token.content.split("\n")):
        marker_line = start_line + offset
        marker = _parse_marker_line(
            line,
            _MarkerLocation(
                marker_line,
                token.level,
                container_path,
                marker_index.document_name,
            ),
        )
        if marker is None:
            continue
        _record_marker(marker_index, marker_line, marker)


def _record_marker(
    marker_index: _MarkerIndex,
    marker_line: int,
    marker: _IndexedMarker,
) -> None:
    """Reject duplicate identifiers and add one marker to the index."""
    if marker.identifier in marker_index.seen_identifiers:
        raise MarkdownExampleError(
            marker_index.document_name,
            marker_line + 1,
            f"duplicate tested-example identifier {marker.identifier!r}",
        )
    marker_index.seen_identifiers.add(marker.identifier)
    marker_index.markers[marker_line] = marker


def _parse_marker_line(
    line: str,
    location: _MarkerLocation,
) -> _IndexedMarker | None:
    """Return a parsed marker line, or ``None`` when the line is unrelated."""
    normalized_line = line.removesuffix("\r")
    indent = len(normalized_line) - len(normalized_line.lstrip(" "))
    marker_text = normalized_line[indent:]
    if indent > _MAX_MARKER_INDENT or not marker_text.startswith(_MARKER_PREFIX):
        return None
    match = _MARKER_PATTERN.fullmatch(marker_text)
    if match is None:
        raise MarkdownExampleError(
            location.document_name,
            location.line_number + 1,
            "tested-example marker needs an identifier",
        )
    return _IndexedMarker(match.group(1), location.token_level, location.container_path)


def _source_lines(token: Token, document_name: str) -> tuple[int, int]:
    """Return a source-mapped fence span or report a parser contract failure."""
    if token.map is None:
        raise MarkdownExampleError(
            document_name, 1, "fenced example has no source location"
        )
    return token.map[0], token.map[1]


def _require_explicit_fence_close(
    token: Token,
    start_line: int,
    end_line: int,
    document_name: str,
) -> None:
    """Reject an unterminated fence, including one stopped by a container edge.

    CommonMark's token map includes both delimiters, while ``content`` includes
    only body lines. The span therefore has two more source lines when closed.

    Raises
    ------
    MarkdownExampleError
        If the fence ends at a CommonMark container boundary or document end.
    """
    body_line_count = token.content.count("\n")
    if token.content and not token.content.endswith("\n"):
        body_line_count += 1
    if end_line - start_line != body_line_count + 2:
        raise MarkdownExampleError(
            document_name,
            start_line + 1,
            "fenced example is unterminated",
        )


def _require_example_marker(
    context: _MarkerContext,
    fence_start: int,
    fence_level: int,
    fence_container_path: _ContainerPath,
) -> tuple[int, str]:
    """Return the nearest marker without intervening block content."""
    for line_gap in (_DIRECT_MARKER_GAP, _BLANK_LINE_MARKER_GAP):
        marker_line = fence_start - line_gap
        marker = context.markers.get(marker_line)
        if marker is None:
            continue
        if marker.level != fence_level:
            continue
        if marker.container_path != fence_container_path:
            continue
        if line_gap == _BLANK_LINE_MARKER_GAP and not _is_blank_container_line(
            context.source_lines[fence_start - 1]
        ):
            continue
        return marker_line, marker.identifier
    raise MarkdownExampleError(
        context.document_name, fence_start + 1, "fenced example is unmarked"
    )


def _is_blank_container_line(line: str) -> bool:
    """Return whether a physical line is blank after Markdown container marks."""
    if not line.strip(" \t\r"):
        return True
    return re.fullmatch(r"(?: {0,3}>[ \t]?)+[ \t\r]*", line) is not None


def _require_language(info: str, fence_start: int, document_name: str) -> str:
    """Return a fence's first info-string word or require a language."""
    language = info.strip().split(maxsplit=1)
    if not language:
        raise MarkdownExampleError(
            document_name, fence_start + 1, "fence needs a language"
        )
    return language[0]


def _check_markers_have_fences(
    markers: dict[int, _IndexedMarker],
    document_name: str,
    consumed_markers: set[int],
) -> None:
    """Reject valid markers that are not attached to a fenced example."""
    dangling_markers = sorted(set(markers) - consumed_markers)
    if dangling_markers:
        line_index = dangling_markers[0]
        identifier = markers[line_index].identifier
        raise MarkdownExampleError(
            document_name,
            line_index + 1,
            f"tested-example marker {identifier!r} has no fence",
        )
