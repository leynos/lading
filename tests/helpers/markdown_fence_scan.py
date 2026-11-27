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
    "clear_deeper_levels",
    "closes_fence",
    "dedent",
    "fence_delimiter",
    "indentation",
    "is_valid_fence_info",
    "list_context",
    "list_item_end",
    "quote_depth",
    "strip_list_items",
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


def _strip_one_quote(line: str, column: int) -> tuple[str, int, bool] | None:
    """Strip one block-quote marker, expanding a following tab.

    Returns
    -------
    tuple[str, int, bool] | None
        The remaining text, the column where it starts, and whether a padding
        space was consumed, or ``None`` when ``line`` opens no marker.
    """
    match = _QUOTE_TOKEN.match(line)
    if match is None:
        return None
    consumed = line[: match.end()]
    new_column = _column_after(consumed, column)
    padding_consumed = consumed.endswith(" ")
    remainder = line[match.end() :]
    if remainder.startswith("\t"):
        # A tab advances to the next four-column stop. Without a padding
        # space the tab itself supplies the marker's one-space padding, so
        # keep only the columns beyond the marker's content column.
        tab_end = (new_column // TAB_WIDTH + 1) * TAB_WIDTH
        content_column = new_column if padding_consumed else new_column + 1
        remainder = " " * max(tab_end - content_column, 0) + remainder[1:]
        return remainder, content_column, padding_consumed
    return remainder, new_column, padding_consumed


def quote_depth(line: str) -> int:
    """Return the number of block-quote markers that prefix ``line``."""
    depth = 0
    remainder = line
    column = 0
    while (step := _strip_one_quote(remainder, column)) is not None:
        remainder, column, _ = step
        depth += 1
    return depth


def strip_quotes(line: str, depth: int) -> str | None:
    """Remove ``depth`` block-quote markers, or ``None`` when the quote ends."""
    remainder = line
    column = 0
    for _ in range(depth):
        step = _strip_one_quote(remainder, column)
        if step is None:
            return None
        remainder, column, _ = step
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
    stripped = line.rstrip("\r\n").strip(" \t")
    run = len(stripped) - len(stripped.lstrip(character))
    return run >= length and stripped == character * run


def indentation(line: str) -> int:
    """Return the width of leading whitespace in columns."""
    stripped = line.lstrip(" \t")
    return _column_after(line[: len(line) - len(stripped)])


def dedent(line: str, indent: int) -> str:
    """Remove up to ``indent`` columns of leading whitespace from ``line``."""
    prefix = line[: len(line) - len(line.lstrip(" \t"))]
    expanded = _expand_tabs(prefix)
    return expanded[indent:] + line[len(prefix) :]


def _expand_tabs(text: str) -> str:
    """Return ``text`` with each tab expanded to the next four-column stop."""
    expanded: list[str] = []
    column = 0
    for character in text:
        if character == " ":
            expanded.append(" ")
            column += 1
            continue
        width = TAB_WIDTH - column % TAB_WIDTH
        expanded.append(" " * width)
        column += width
    return "".join(expanded)


def clear_deeper_levels(levels: dict[int, list[int]], depth: int) -> None:
    """Discard list levels scoped to containers deeper than ``depth``."""
    for stale in [key for key in levels if key > depth]:
        del levels[stale]


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
    match = _list_item(content, levels[-1] if levels else 0)
    if match is not None:
        content_start = _list_content_start(content, match)
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
    remainder, _content_column = strip_list_items(content, list_indent)
    if remainder != content:
        too_indented = indentation(remainder) > MAX_FENCE_INDENT
    else:
        too_indented = indentation(remainder) > list_indent + MAX_FENCE_INDENT
    if too_indented:
        return None
    return fence_delimiter(remainder.strip(" \t"))


def strip_list_items(content: str, container_indent: int) -> tuple[str, int]:
    """Strip every leading list marker, returning the rest and its column.

    CommonMark allows nested list containers to open on one line, so
    ``- - content`` has two markers.

    Returns
    -------
    tuple[str, int]
        The remaining content and the column where it begins.
    """
    remainder = content
    base = 0
    container = container_indent
    matched = False
    while (match := _list_item(remainder, container)) is not None:
        content_start = _list_content_start(remainder, match)
        base = _column_after(remainder[:content_start], base)
        remainder = remainder[content_start:]
        container = base
        matched = True
    return remainder, base if matched else container_indent


def _list_item(content: str, container_indent: int) -> re.Match[str] | None:
    """Return a list-item match indented within its container, or ``None``.

    Four or more spaces of indentation beyond the container make indented
    code, not a list item.

    Returns
    -------
    re.Match[str] | None
        The list-item match when the marker sits within its container.
    """
    match = _LIST_ITEM.match(content)
    if match is None:
        return None
    if indentation(content[: match.end()]) > container_indent + MAX_FENCE_INDENT:
        return None
    return match


def list_item_end(content: str, container_indent: int = 0) -> int | None:
    """Return the content start of a leading list item, or ``None``.

    Parameters
    ----------
    content : str
        The line content after any block-quote markers.
    container_indent : int, optional
        The content column of the enclosing container.

    Returns
    -------
    int | None
        The offset where the item's content begins, after at most one space of
        marker padding when more than four spaces follow the marker, or
        ``None`` when ``content`` does not start with a list item within
        ``container_indent``.
    """
    match = _list_item(content, container_indent)
    return _list_content_start(content, match) if match is not None else None


def _list_content_start(content: str, match: re.Match[str]) -> int:
    """Return the offset where a matched list item's content begins."""
    marker_end = match.start("space")
    marker_column = _column_after(content[:marker_end])
    padding_width = _column_after(match.group("space"), marker_column)
    if padding_width - marker_column > MAX_LIST_PADDING:
        # CommonMark assigns one space as padding and treats the rest as
        # content indentation, which can make an apparent fence literal code.
        return marker_end + 1
    return match.end()


def _column_after(text: str, start_column: int = 0) -> int:
    """Return the column reached after ``text`` with four-column tab stops."""
    column = start_column
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
