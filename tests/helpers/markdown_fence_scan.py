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
    """Return the number of leading spaces before any other character."""
    return len(line) - len(line.lstrip(" "))


def dedent(line: str, indent: int) -> str:
    """Remove up to ``indent`` leading spaces from ``line``."""
    removed = 0
    while removed < indent and line.startswith(" "):
        line = line[1:]
        removed += 1
    return line


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
        indent = match.end()
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
        base = list_item.end()
        remainder = content[base:]
    else:
        base = list_indent
        remainder = content
    if indentation(remainder) > base + MAX_FENCE_INDENT:
        return None
    return fence_delimiter(remainder.strip())


def list_item_end(content: str) -> int | None:
    """Return the end of a leading list-item marker, or ``None``.

    Returns
    -------
    int | None
        The offset just past the marker and its following whitespace, or
        ``None`` when ``content`` does not start with a list item.
    """
    match = _LIST_ITEM.match(content)
    return match.end() if match is not None else None


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
        if closes_fence(content, character, length, list_indent):
            return end + 1
    return len(lines)
