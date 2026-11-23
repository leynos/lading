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
    """Yield document lines outside all fenced code bodies."""
    open_fence: tuple[str, int] | None = None
    for line_index, line in enumerate(lines):
        if open_fence is not None:
            if _is_closing_fence(line, open_fence):
                open_fence = None
            continue
        opening = _OPENING_FENCE_PATTERN.fullmatch(line)
        if opening is None:
            yield line_index, line
            continue
        marker = opening.group("fence")
        open_fence = (marker[0], len(marker))


def _is_closing_fence(line: str, open_fence: tuple[str, int]) -> bool:
    """Return whether ``line`` closes the active fence family and length."""
    closing = _CLOSING_FENCE_PATTERN.fullmatch(line)
    if closing is None:
        return False
    marker = closing.group("fence")
    return marker[0] == open_fence[0] and len(marker) >= open_fence[1]


def _extract_examples(
    lines: list[str], document_name: str
) -> tuple[list[MarkdownExample], set[int]]:
    """Extract marked fences and return the marker lines they consumed."""
    examples: list[MarkdownExample] = []
    consumed_markers: set[int] = set()
    line_index = 0
    while line_index < len(lines):
        if _OPENING_FENCE_PATTERN.fullmatch(lines[line_index]) is None:
            line_index += 1
            continue
        example, marker_index, closing_index = _parse_example_at(
            lines, line_index, document_name
        )
        examples.append(example)
        consumed_markers.add(marker_index)
        line_index = closing_index + 1
    return examples, consumed_markers


def _parse_example_at(
    lines: list[str], fence_index: int, document_name: str
) -> tuple[MarkdownExample, int, int]:
    """Parse one opening fence, its marker and matching closing fence."""
    opening = _require_opening_fence(lines, fence_index, document_name)
    marker_index, identifier = _require_example_marker(
        lines, fence_index, document_name
    )
    closing_index = _require_closing_fence(
        lines, fence_index, opening.group("fence"), document_name
    )
    example = MarkdownExample(
        identifier=identifier,
        language=opening.group("info").strip().split(maxsplit=1)[0],
        source="\n".join(
            _remove_fence_indentation(
                lines[fence_index + 1 : closing_index],
                len(opening.group("indent")),
            )
        ),
    )
    return example, marker_index, closing_index


def _require_opening_fence(
    lines: list[str], fence_index: int, document_name: str
) -> re.Match[str]:
    """Return a valid opening fence with a language identifier."""
    opening = _OPENING_FENCE_PATTERN.fullmatch(lines[fence_index])
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
    match = _MARKER_PATTERN.fullmatch(lines[marker_index])
    if match is None:
        raise MarkdownExampleError(
            document_name, marker_index + 1, "tested-example marker needs an identifier"
        )
    return marker_index, match.group(1)


def _require_closing_fence(
    lines: list[str],
    fence_index: int,
    opening_fence: str,
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
        match = _MARKER_PATTERN.fullmatch(lines[line_index])
        identifier = match.group(1) if match is not None else "<invalid>"
        raise MarkdownExampleError(
            document_name,
            line_index + 1,
            f"tested-example marker {identifier!r} has no fence",
        )


def _preceding_marker_index(lines: list[str], fence_index: int) -> int | None:
    """Return an adjacent marker index, allowing its usual separating blank."""
    preceding_index = fence_index - 1
    if preceding_index >= 0 and _MARKER_PATTERN.fullmatch(lines[preceding_index]):
        return preceding_index
    if preceding_index > 0 and not lines[preceding_index]:
        marker_index = preceding_index - 1
        if _MARKER_PATTERN.fullmatch(lines[marker_index]):
            return marker_index
    return None


def _find_closing_fence(lines: list[str], start: int, opening_fence: str) -> int | None:
    """Find a closing fence with the same marker and at least its length."""
    for index in range(start, len(lines)):
        closing = _CLOSING_FENCE_PATTERN.fullmatch(lines[index])
        if closing is None:
            continue
        closing_fence = closing.group("fence")
        if closing_fence[0] != opening_fence[0]:
            continue
        if len(closing_fence) >= len(opening_fence):
            return index
    return None


def _remove_fence_indentation(lines: list[str], indentation: int) -> list[str]:
    """Remove up to the opening fence's indentation from each source line."""
    if indentation == 0:
        return lines
    return [
        line[min(indentation, len(line) - len(line.lstrip(" "))) :] for line in lines
    ]
