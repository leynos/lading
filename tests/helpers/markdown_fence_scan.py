"""Low-level Markdown fence and container scanning helpers.

These helpers decode block-quote depth, list-item containers, fence
delimiters, and matching closing fences. The tested-example loader builds its
marker and example semantics on top of them.
"""

import collections.abc as cabc
import re
import typing as typ

__all__ = [
    "MAX_FENCE_INDENT",
    "closes_fence",
    "dedent",
    "fence_delimiter",
    "indentation",
    "is_valid_fence_info",
    "list_context",
    "list_item_end",
    "quote_depth",
    "strip_quotes",
    "unmarked_fence_delimiter",
    "unmarked_fence_end",
    "without_container",
]

MIN_FENCE_LENGTH: typ.Final[int] = 3
MAX_FENCE_INDENT: typ.Final[int] = 3
MAX_LIST_PADDING: typ.Final[int] = 4
TAB_WIDTH: typ.Final[int] = 4
_CONTAINER_MARKERS: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:>\s?)|(?:[-+*]|\d{1,9}[.)])\s+)+"
)
_QUOTE_TOKEN: typ.Final[re.Pattern[str]] = re.compile(r"^ {0,3}> ?")
_LIST_ITEM: typ.Final[re.Pattern[str]] = re.compile(
    r"^(?P<indent> *)(?:[-+*]|\d{1,9}[.)])(?P<space>[ \t]+)"
)


def without_container(line: str) -> str:
    """Return ``line`` without leading block-quote or list-item markers."""
    return _CONTAINER_MARKERS.sub("", line).strip()


def quote_depth(line: str) -> int:
    """Return the number of block-quote markers that prefix ``line``."""
    depth = 0
    remainder = line
    while (match := _QUOTE_TOKEN.match(remainder)) is not None:
        remainder = remainder[match.end() :]
        depth += 1
    return depth


def strip_quotes(line: str, depth: int) -> str | None:
    """Remove ``depth`` block-quote markers, or ``None`` when the quote ends."""
    remainder = line
    for _ in range(depth):
        match = _QUOTE_TOKEN.match(remainder)
        if match is None:
            return None
        remainder = remainder[match.end() :]
    return remainder


def fence_delimiter(line: str) -> tuple[str, int] | None:
    """Return the fence character and run length for an opening fence."""
    for character in ("`", "~"):
        length = len(line) - len(line.lstrip(character))
        if length < MIN_FENCE_LENGTH or not line.startswith(character * length):
            continue
        if is_valid_fence_info(character, line[length:]):
            return character, length
    return None


def is_valid_fence_info(character: str, info: str) -> bool:
    r"""Return whether ``info`` may follow a fence opener of ``character``.

    CommonMark forbids backticks in a backtick fence's info string, so
    ``\`\`\`bash\`\`\``` is inline code, not a fence. Tilde fences may carry
    backticks in their info string.

    Returns
    -------
    bool
        ``True`` when the info string is valid for the fence character.
    """
    return character != "`" or "`" not in info


def closes_fence(
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
    allowed = container_indent + MAX_FENCE_INDENT
    if indentation(line) > allowed:
        return False
    stripped = line.strip()
    run = len(stripped) - len(stripped.lstrip(character))
    return run >= length and stripped == character * run


def indentation(line: str) -> int:
    """Return the width of leading whitespace in columns."""
    stripped = line.lstrip(" \t")
    return _column_after(line[: len(line) - len(stripped)])


def dedent(line: str, indent: int) -> str:
    """Remove up to ``indent`` columns of leading whitespace from ``line``."""
    column = 0
    index = 0
    while index < len(line) and column < indent and line[index] in " \t":
        column = (
            column + 1 if line[index] != "\t" else (column // TAB_WIDTH + 1) * TAB_WIDTH
        )
        index += 1
    return line[index:]


def list_context(raw: str, levels: list[int]) -> int:
    """Return the content indentation of the enclosing list item, if any.

    ``levels`` holds the content indentation of each open list level. An
    outdented line closes deeper levels, restoring the matching ancestor.

    Returns
    -------
    int
        The content indentation of the enclosing list item, or ``0`` outside a
        list.
    """
    depth = quote_depth(raw)
    content = strip_quotes(raw, depth) or ""
    match = _LIST_ITEM.match(content)
    if match is not None:
        content_start = _list_content_start(match)
        indent = _column_after(content[:content_start])
        while levels and levels[-1] >= indent:
            levels.pop()
        levels.append(indent)
        return indent
    if content.strip():
        content_indentation = indentation(content)
        while levels and levels[-1] > content_indentation:
            levels.pop()
    return levels[-1] if levels else 0


def unmarked_fence_delimiter(
    raw: str,
    list_indent: int,
) -> tuple[str, int] | None:
    """Return the fence delimiter opening ``raw``, or ``None``.

    A delimiter indented beyond its container's limit is literal code, not a
    fence, so it is ignored.

    Returns
    -------
    tuple[str, int] | None
        The fence character and run length, or ``None`` when ``raw`` opens no
        fence.
    """
    depth = quote_depth(raw)
    content = strip_quotes(raw, depth) or ""
    list_item = _LIST_ITEM.match(content)
    if list_item is not None:
        remainder = content[_list_content_start(list_item) :]
        too_indented = indentation(remainder) > MAX_FENCE_INDENT
    else:
        remainder = content
        too_indented = indentation(remainder) > list_indent + MAX_FENCE_INDENT
    if too_indented:
        return None
    return fence_delimiter(remainder.strip())


def list_item_end(content: str) -> int | None:
    """Return the content start of a leading list item, or ``None``.

    Returns
    -------
    int | None
        The offset where the item's content begins, after at most one space of
        marker padding when more than four spaces follow the marker, or
        ``None`` when ``content`` does not start with a list item.
    """
    match = _LIST_ITEM.match(content)
    return _list_content_start(match) if match is not None else None


def _list_content_start(match: re.Match[str]) -> int:
    """Return the offset where a matched list item's content begins."""
    padding_width = _column_after(match.group("space"))
    if padding_width > MAX_LIST_PADDING:
        # CommonMark assigns one space as padding and treats the rest as
        # content indentation, which can make an apparent fence literal code.
        return match.start("space") + 1
    return match.end()


def _column_after(text: str) -> int:
    """Return the column reached after ``text`` with four-column tab stops."""
    column = 0
    for character in text:
        column = (
            column + 1 if character != "\t" else (column // TAB_WIDTH + 1) * TAB_WIDTH
        )
    return column


def unmarked_fence_end(
    lines: cabc.Sequence[str],
    index: int,
    list_indent: int,
) -> int | None:
    """Return the index after an unmarked fence opened at ``index``.

    Returns
    -------
    int | None
        The index just past the closing fence, ``len(lines)`` when the fence
        never closes, or ``None`` when ``index`` does not open a fence.
    """
    delimiter = unmarked_fence_delimiter(lines[index], list_indent)
    if delimiter is None:
        return None
    character, length = delimiter
    depth = quote_depth(lines[index])
    for end in range(index + 1, len(lines)):
        content = strip_quotes(lines[end], depth)
        if content is None:
            return end
        if content.strip() and indentation(content) < list_indent:
            # An outdented line ends the list item, and with it the fence.
            return end
        if closes_fence(content, character, length, list_indent):
            return end + 1
    return len(lines)
