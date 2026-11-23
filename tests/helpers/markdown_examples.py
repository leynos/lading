"""Load marked fenced examples from published Markdown documents."""

import collections.abc as cabc
import dataclasses as dc
import re
from pathlib import Path

_MARKER_PREFIX = "<!-- tested-example:"
_MARKER_PATTERN = re.compile(r"<!-- tested-example: ([a-z0-9][a-z0-9_-]*) -->")
_OPENING_FENCE_PATTERN = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$"
)
_CLOSING_FENCE_PATTERN = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})[ \t]*$")
_BLOCKQUOTE_PREFIX_PATTERN = re.compile(r"^ {0,3}> ?")


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
    """Parse a complete document and enforce unique markers for every fence."""
    lines = document.splitlines()
    markers = _index_markers(lines, document_name)
    examples, consumed_markers = _extract_examples(lines, document_name)
    _check_markers_have_fences(lines, document_name, markers, consumed_markers)
    return tuple(examples)


def _index_markers(lines: list[str], document_name: str) -> dict[str, int]:
    """Index valid markers and reject missing or duplicate identifiers."""
    markers: dict[str, int] = {}
    for line_index, line in _lines_outside_fences(lines):
        if not line.startswith(_MARKER_PREFIX):
            continue
        match = _MARKER_PATTERN.fullmatch(line)
        if match is None:
            raise MarkdownExampleError(
                document_name,
                line_index + 1,
                "tested-example marker needs an identifier",
            )
        identifier = match.group(1)
        if identifier in markers:
            raise MarkdownExampleError(
                document_name,
                line_index + 1,
                f"duplicate tested-example identifier {identifier!r}",
            )
        markers[identifier] = line_index
    return markers


def _lines_outside_fences(lines: list[str]) -> cabc.Iterator[tuple[int, str]]:
    """Yield normalized document lines outside all fenced code bodies."""
    open_fence: tuple[str, int, int] | None = None
    for line_index, line in enumerate(lines):
        if open_fence is not None:
            if _is_closing_fence(line, open_fence):
                open_fence = None
            continue
        normalized_line, quote_depth = _strip_blockquote_prefixes(line)
        opening = _OPENING_FENCE_PATTERN.fullmatch(normalized_line)
        if opening is None:
            yield line_index, normalized_line
            continue
        marker = opening.group("fence")
        open_fence = (marker[0], len(marker), quote_depth)


def _is_closing_fence(line: str, open_fence: tuple[str, int, int]) -> bool:
    """Return whether ``line`` closes the active fence family and length."""
    content = _line_in_quote_container(line, open_fence[2])
    if content is None:
        return False
    closing = _CLOSING_FENCE_PATTERN.fullmatch(content)
    if closing is None:
        return False
    marker = closing.group("fence")
    return marker[0] == open_fence[0] and len(marker) >= open_fence[1]


def _strip_blockquote_prefixes(
    line: str, *, limit: int | None = None
) -> tuple[str, int]:
    """Remove blockquote container markers while preserving their nesting depth."""
    depth = 0
    while limit is None or depth < limit:
        prefix = _BLOCKQUOTE_PREFIX_PATTERN.match(line)
        if prefix is None:
            break
        line = line[prefix.end() :]
        depth += 1
    return line, depth


def _line_in_quote_container(line: str, quote_depth: int) -> str | None:
    """Return line content inside the active quote, or ``None`` outside it."""
    if quote_depth == 0:
        return line
    content, depth = _strip_blockquote_prefixes(line, limit=quote_depth)
    if depth == quote_depth:
        return content
    return "" if not line.strip() else None


def _extract_examples(
    lines: list[str], document_name: str
) -> tuple[list[MarkdownExample], set[int]]:
    """Extract marked fences and return the marker lines they consumed."""
    examples: list[MarkdownExample] = []
    consumed_markers: set[int] = set()
    line_index = 0
    while line_index < len(lines):
        normalized_line, quote_depth = _strip_blockquote_prefixes(lines[line_index])
        if _OPENING_FENCE_PATTERN.fullmatch(normalized_line) is None:
            line_index += 1
            continue
        example, marker_index, closing_index = _parse_example_at(
            lines, line_index, quote_depth, document_name
        )
        examples.append(example)
        consumed_markers.add(marker_index)
        line_index = closing_index + 1
    return examples, consumed_markers


def _parse_example_at(
    lines: list[str],
    fence_index: int,
    quote_depth: int,
    document_name: str,
) -> tuple[MarkdownExample, int, int]:
    """Parse one opening fence, its marker and matching closing fence."""
    opening = _require_opening_fence(lines, fence_index, document_name)
    marker_index, identifier = _require_example_marker(
        lines, fence_index, document_name
    )
    marker = opening.group("fence")
    closing_index = _require_closing_fence(
        lines, fence_index, (marker[0], len(marker), quote_depth), document_name
    )
    source_lines = [
        _source_line_in_container(line, quote_depth)
        for line in lines[fence_index + 1 : closing_index]
    ]
    example = MarkdownExample(
        identifier=identifier,
        language=opening.group("info").strip().split(maxsplit=1)[0],
        source="\n".join(
            _remove_fence_indentation(
                source_lines,
                len(opening.group("indent")),
            )
        ),
    )
    return example, marker_index, closing_index


def _source_line_in_container(line: str, quote_depth: int) -> str:
    """Strip the fence's quote containers from a source line when present."""
    content = _line_in_quote_container(line, quote_depth)
    return line if content is None else content


def _require_opening_fence(
    lines: list[str], fence_index: int, document_name: str
) -> re.Match[str]:
    """Return a valid opening fence with a language identifier."""
    content, _ = _strip_blockquote_prefixes(lines[fence_index])
    opening = _OPENING_FENCE_PATTERN.fullmatch(content)
    if opening is None:  # Called only after _extract_examples identified a fence.
        raise MarkdownExampleError(document_name, fence_index + 1, "expected a fence")
    info = opening.group("info").strip()
    if not info:
        raise MarkdownExampleError(
            document_name, fence_index + 1, "fence needs a language"
        )
    return opening


def _require_example_marker(
    lines: list[str], fence_index: int, document_name: str
) -> tuple[int, str]:
    """Return the stable identifier attached to a fenced example."""
    marker_index = _preceding_marker_index(lines, fence_index)
    if marker_index is None:
        raise MarkdownExampleError(
            document_name, fence_index + 1, "fenced example is unmarked"
        )
    marker_line, _ = _strip_blockquote_prefixes(lines[marker_index])
    match = _MARKER_PATTERN.fullmatch(marker_line)
    if match is None:
        raise MarkdownExampleError(
            document_name, marker_index + 1, "tested-example marker needs an identifier"
        )
    return marker_index, match.group(1)


def _require_closing_fence(
    lines: list[str],
    fence_index: int,
    opening_fence: tuple[str, int, int],
    document_name: str,
) -> int:
    """Return the matching close or report the unterminated example."""
    closing_index = _find_closing_fence(lines, fence_index + 1, opening_fence)
    if closing_index is None:
        raise MarkdownExampleError(
            document_name,
            fence_index + 1,
            "fenced example is unterminated",
        )
    return closing_index


def _check_markers_have_fences(
    lines: list[str],
    document_name: str,
    markers: dict[str, int],
    consumed_markers: set[int],
) -> None:
    """Reject valid markers that are not attached to a fenced example."""
    dangling_markers = sorted(set(markers.values()) - consumed_markers)
    if dangling_markers:
        line_index = dangling_markers[0]
        marker_line, _ = _strip_blockquote_prefixes(lines[line_index])
        match = _MARKER_PATTERN.fullmatch(marker_line)
        identifier = match.group(1) if match is not None else "<invalid>"
        raise MarkdownExampleError(
            document_name,
            line_index + 1,
            f"tested-example marker {identifier!r} has no fence",
        )


def _preceding_marker_index(lines: list[str], fence_index: int) -> int | None:
    """Return an adjacent marker index, allowing its usual separating blank."""
    preceding_index = fence_index - 1
    if preceding_index >= 0:
        if _is_tested_marker(lines[preceding_index]):
            return preceding_index
        preceding_line, _ = _strip_blockquote_prefixes(lines[preceding_index])
        if preceding_index > 0 and not preceding_line:
            marker_index = preceding_index - 1
            if _is_tested_marker(lines[marker_index]):
                return marker_index
    return None


def _is_tested_marker(line: str) -> bool:
    """Return whether a structural line contains a valid tested marker."""
    content, _ = _strip_blockquote_prefixes(line)
    return _MARKER_PATTERN.fullmatch(content) is not None


def _find_closing_fence(
    lines: list[str], start: int, opening_fence: tuple[str, int, int]
) -> int | None:
    """Find a closing fence with the same marker and at least its length."""
    for index in range(start, len(lines)):
        content = _line_in_quote_container(lines[index], opening_fence[2])
        if content is None:
            continue
        closing = _CLOSING_FENCE_PATTERN.fullmatch(content)
        if closing is None:
            continue
        closing_fence = closing.group("fence")
        if closing_fence[0] != opening_fence[0]:
            continue
        if len(closing_fence) >= opening_fence[1]:
            return index
    return None


def _remove_fence_indentation(lines: list[str], indentation: int) -> list[str]:
    """Remove up to the opening fence's indentation from each source line."""
    if indentation == 0:
        return lines
    return [
        line[min(indentation, len(line) - len(line.lstrip(" "))) :] for line in lines
    ]
