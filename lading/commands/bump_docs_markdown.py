"""Markdown fence discovery and rendering for documentation bumps.

:func:`replace_markdown_fences` selects fenced blocks of one language with
``markdown-it-py`` and re-renders each transformed body with its original info
string, indentation, block-quote or list prefix, and line endings. The TOML
version policy that decides what to transform lives in
:mod:`lading.commands.bump_docs_fences`.
"""

import collections.abc as cabc
import re
import typing as typ

from markdown_it import MarkdownIt

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from markdown_it.token import Token

__all__ = [
    "extract_fence_indent",
    "render_fence",
    "replace_markdown_fences",
    "token_matches_language",
]


_SOURCE_LINE: typ.Final[re.Pattern[str]] = re.compile(
    r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$"
)


def _source_lines(markdown_text: str) -> list[str]:
    """Split ``markdown_text`` on CR, LF, and CRLF only.

    ``str.splitlines`` also splits on U+2028 and other separators that
    MarkdownIt does not treat as line breaks, which would misalign token maps.

    Returns
    -------
    list[str]
        The source lines, each retaining its own ending.
    """
    return _SOURCE_LINE.findall(markdown_text)


def replace_markdown_fences(
    markdown_text: str,
    language: str,
    transform: cabc.Callable[[str], str],
) -> str:
    """Replace fenced code blocks of ``language`` with ``transform``.

    Parameters
    ----------
    markdown_text
        The Markdown source text to process.
    language
        The language identifier for code fences to match (e.g., "toml").
    transform
        A callable that receives fence content and returns transformed content.

    Returns
    -------
    str
        The Markdown text with matching fences replaced by transformed content.

    """
    parser = MarkdownIt("commonmark")
    tokens = parser.parse(markdown_text)
    lines = _source_lines(markdown_text)
    output: list[str] = []
    last_index = 0
    for token in tokens:
        if not token_matches_language(token, language) or token.map is None:
            continue
        start, end = token.map
        output.append("".join(lines[last_index:start]))
        output.append(render_fence(token, lines, language, transform))
        last_index = end
    output.append("".join(lines[last_index:]))
    return "".join(output)


def token_matches_language(token: Token, language: str) -> bool:
    """Return ``True`` when ``token`` is a fence with ``language``.

    Parameters
    ----------
    token
        A markdown-it token to inspect.
    language
        The language identifier to match against.

    Returns
    -------
    bool
        True if the token is a fence block with the specified language.

    """
    if token.type != "fence":
        return False
    info = (token.info or "").split()
    info_lang = info[0].lower() if info else ""
    return info_lang == language.lower()


def render_fence(
    token: Token,
    lines: list[str],
    language: str,
    transform: cabc.Callable[[str], str],
) -> str:
    """Return a rewritten fence for ``token`` using ``transform``.

    Parameters
    ----------
    token
        A markdown-it fence token with map data.
    lines
        The original Markdown text split into lines with endings preserved.
    language
        The language identifier for the fence.
    transform
        A callable that receives fence content and returns transformed content.

    Returns
    -------
    str
        The rendered fence block with transformed content.

    Raises
    ------
    ValueError
        If the token is missing map data.

    """
    if token.map is None:
        message = "Fence token missing map data"
        raise ValueError(message)
    start, end = token.map
    original = "".join(lines[start:end])
    new_body = transform(token.content)
    if new_body == token.content:
        return original
    opener = lines[start]
    marker = token.markup or "```"
    prefix = extract_fence_indent(opener, marker)
    continuation = _continuation_indent(prefix)
    ending = _line_ending(opener) or "\n"
    opener = _terminated_opener(opener, ending)
    closer = lines[end - 1] if end > start + 1 else ""
    if not _is_closing_fence(closer, continuation, marker):
        closer = f"{continuation}{marker}{ending}"
    body_text = new_body.rstrip("\r\n")
    rendered = "".join(
        f"{continuation}{line}{ending}" for line in body_text.split("\n")
    )
    return opener + rendered + closer


def _terminated_opener(opener: str, ending: str) -> str:
    """Return the opener with a line ending, so the body starts its own line.

    An opener that ends the source has no line ending of its own; without one
    the generated body would be spliced onto the fence marker.

    Returns
    -------
    str
        The opener, terminated with ``ending`` when it had none.
    """
    if _line_ending(opener):
        return opener
    return f"{opener}{ending}"


_QUOTE_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^(?: {0,3}> ?)+")
_LIST_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^ *(?:[-+*]|\d{1,9}[.)])[ \t]+")


def _continuation_indent(prefix: str) -> str:
    """Return the indentation a transformed body line needs.

    Block-quote markers are repeated. A list marker is replaced by spaces that
    reach the same column so the body stays in the list item without another
    bullet, while any enclosing or following block quote is preserved. Tabs
    advance to the next multiple of four columns, as CommonMark specifies.

    Returns
    -------
    str
        The prefix to place before each transformed body line.
    """
    result: list[str] = []
    rest = prefix
    column = 0
    while rest:
        quote = _QUOTE_PREFIX.match(rest)
        if quote is not None:
            piece = quote.group(0)
            result.append(piece)
            column = _advance_columns(piece, column)
            rest = rest[quote.end() :]
            continue
        list_marker = _LIST_PREFIX.match(rest)
        if list_marker is not None:
            piece = list_marker.group(0)
            marker_end = _advance_columns(piece, column)
            result.append(" " * (marker_end - column))
            column = marker_end
            rest = rest[list_marker.end() :]
            continue
        break
    if not rest.strip():
        result.append(rest)
        return "".join(result)
    remainder_end = _advance_columns(rest, column)
    return f"{''.join(result)}{' ' * (remainder_end - column)}"


def _advance_columns(text: str, column: int = 0) -> int:
    """Return the column reached after ``text`` when tabs stop every four."""
    for character in text:
        column = column + 1 if character != "\t" else (column // 4 + 1) * 4
    return column


def _is_closing_fence(line: str, prefix: str, marker: str) -> bool:
    """Return whether ``line`` is a closing fence for ``marker``.

    Only the fence character, a run at least as long as the opener, and
    trailing whitespace are accepted; a body line that merely mentions the
    marker is not a closer.

    Returns
    -------
    bool
        Whether ``line`` closes the fence.
    """
    content = line[len(prefix) :] if line.startswith(prefix) else line.lstrip()
    stripped = content.strip()
    character = marker[0]
    run = len(stripped) - len(stripped.lstrip(character))
    return run >= len(marker) and stripped == character * run


def _line_ending(line: str) -> str:
    """Return ``line``'s ending, or an empty string when it has none.

    Returns
    -------
    str
        The CRLF, LF, or CR ending, or an empty string when the line has none.
    """
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return ending
    return ""


def extract_fence_indent(line: str, fence_marker: str) -> str:
    """Return the text preceding ``fence_marker`` in ``line``.

    Parameters
    ----------
    line
        A line of text potentially containing a fence marker.
    fence_marker
        The fence marker string to search for (e.g., "```").

    Returns
    -------
    str
        The text before the fence marker, or an empty string if not found.
    """
    position = line.find(fence_marker)
    return "" if position < 0 else line[:position]
