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
    """Parse CommonMark containers and enforce unique markers for every fence."""
    tokens = MarkdownIt("commonmark").parse(document)
    markers = _index_markers(tokens, document_name)
    examples: list[MarkdownExample] = []
    consumed_markers: set[int] = set()
    for token in tokens:
        if token.type != "fence":
            continue
        start_line, end_line = _source_lines(token, document_name)
        _require_explicit_fence_close(token, start_line, end_line, document_name)
        marker_line, identifier = _require_example_marker(
            markers, start_line, token.level, document_name
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
    _check_markers_have_fences(markers, document_name, consumed_markers)
    return tuple(examples)


def _index_markers(
    tokens: cabc.Sequence[Token], document_name: str
) -> dict[int, tuple[str, int]]:
    """Index valid marker comments and reject missing or duplicate identifiers."""
    markers: dict[int, tuple[str, int]] = {}
    seen_identifiers: set[str] = set()
    for token in tokens:
        if token.type != "html_block" or token.map is None:
            continue
        start_line = token.map[0]
        for offset, line in enumerate(token.content.splitlines()):
            if not line.startswith(_MARKER_PREFIX):
                continue
            line_number = start_line + offset
            match = _MARKER_PATTERN.fullmatch(line)
            if match is None:
                raise MarkdownExampleError(
                    document_name,
                    line_number + 1,
                    "tested-example marker needs an identifier",
                )
            identifier = match.group(1)
            if identifier in seen_identifiers:
                raise MarkdownExampleError(
                    document_name,
                    line_number + 1,
                    f"duplicate tested-example identifier {identifier!r}",
                )
            seen_identifiers.add(identifier)
            markers[line_number] = (identifier, token.level)
    return markers


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
    markers: dict[int, tuple[str, int]],
    fence_start: int,
    fence_level: int,
    document_name: str,
) -> tuple[int, str]:
    """Return the marker immediately before a fence at the same container level."""
    for marker_line in (fence_start - 1, fence_start - 2):
        marker = markers.get(marker_line)
        if marker is not None and marker[1] == fence_level:
            return marker_line, marker[0]
    raise MarkdownExampleError(
        document_name, fence_start + 1, "fenced example is unmarked"
    )


def _require_language(info: str, fence_start: int, document_name: str) -> str:
    """Return a fence's first info-string word or require a language."""
    language = info.strip().split(maxsplit=1)
    if not language:
        raise MarkdownExampleError(
            document_name, fence_start + 1, "fence needs a language"
        )
    return language[0]


def _check_markers_have_fences(
    markers: dict[int, tuple[str, int]],
    document_name: str,
    consumed_markers: set[int],
) -> None:
    """Reject valid markers that are not attached to a fenced example."""
    dangling_markers = sorted(set(markers) - consumed_markers)
    if dangling_markers:
        line_index = dangling_markers[0]
        identifier, _ = markers[line_index]
        raise MarkdownExampleError(
            document_name,
            line_index + 1,
            f"tested-example marker {identifier!r} has no fence",
        )
