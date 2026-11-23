"""Load marked fenced examples from published Markdown documents."""

import dataclasses as dc
import re
from pathlib import Path

_MARKER_PREFIX = "<!-- tested-example:"
_MARKER_PATTERN = re.compile(r"<!-- tested-example: ([a-z0-9][a-z0-9_-]*) -->")
_OPENING_FENCE_PATTERN = re.compile(r"^(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_CLOSING_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})\s*$")


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
    for line_index, line in enumerate(lines):
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
    opening = _OPENING_FENCE_PATTERN.fullmatch(lines[fence_index])
    if opening is None:  # Called only after _extract_examples identified a fence.
        raise MarkdownExampleError(document_name, fence_index + 1, "expected a fence")
    opening_fence = opening.group("fence")
    info = opening.group("info").strip()
    if not info:
        raise MarkdownExampleError(
            document_name, fence_index + 1, "fence needs a language"
        )
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
    closing_index = _find_closing_fence(lines, fence_index + 1, opening_fence)
    if closing_index is None:
        raise MarkdownExampleError(
            document_name,
            fence_index + 1,
            f"fenced example {match.group(1)!r} is unterminated",
        )
    return (
        MarkdownExample(
            identifier=match.group(1),
            language=info.split(maxsplit=1)[0],
            source="\n".join(lines[fence_index + 1 : closing_index]),
        ),
        marker_index,
        closing_index,
    )


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
        closing_fence = closing.group(1)
        if closing_fence[0] != opening_fence[0]:
            continue
        if len(closing_fence) >= len(opening_fence):
            return index
    return None
