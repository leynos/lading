"""Markdown fence processing for documentation version bumps.

The functions here find fenced TOML blocks, rewrite the version references they
contain, and re-render the fence with its original info string and indentation.
They are independent of filesystem discovery, which
:mod:`lading.commands.bump_docs` owns.
"""

import collections.abc as cabc
import re
import typing as typ

from markdown_it import MarkdownIt
from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import bump_toml

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from markdown_it.token import Token
    from tomlkit.toml_document import TOMLDocument
else:  # pragma: no cover - provide runtime placeholders for type checking imports
    Token = TOMLDocument = typ.Any

__all__ = [
    "extract_fence_indent",
    "render_fence",
    "replace_markdown_fences",
    "rewrite_markdown_toml_fences",
    "token_matches_language",
    "update_toml_snippet_dependencies",
    "update_toml_snippet_versions",
]


def rewrite_markdown_toml_fences(
    markdown_text: str,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> tuple[str, bool]:
    """Rewrite TOML fences for ``dependency_targets`` within Markdown text.

    Parameters
    ----------
    markdown_text
        The Markdown source text containing TOML code fences.
    dependency_targets
        Names of crates whose dependency entries should be updated.
    target_version
        The version string to apply to matching entries.

    Returns
    -------
    tuple[str, bool]
        A tuple of (updated_text, changed) where changed indicates whether
        any modifications were made.

    """
    changed = False

    def _apply(snippet: str) -> str:
        """Apply dependency version rewrites to one TOML fence."""
        nonlocal changed
        replacement, snippet_changed = update_toml_snippet_versions(
            snippet, dependency_targets, target_version
        )
        if snippet_changed:
            changed = True
        return replacement

    updated = replace_markdown_fences(markdown_text, "toml", _apply)
    return updated, changed


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
    closer = lines[end - 1] if end > start + 1 else ""
    if not _is_closing_fence(closer, continuation, marker):
        closer = f"{continuation}{marker}{ending}"
    body_text = new_body.rstrip("\r\n")
    rendered = "".join(
        f"{continuation}{line}{ending}" for line in body_text.split("\n")
    )
    return opener + rendered + closer


_QUOTE_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^(?: {0,3}> ?)+")
_LIST_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^ *(?:[-+*]|\d{1,9}[.)])[ \t]+")


def _continuation_indent(prefix: str) -> str:
    """Return the indentation a transformed body line needs.

    Block-quote markers are repeated. A list marker is replaced by spaces of
    the same width so the body stays in the list item without another bullet,
    while any enclosing or following block quote is preserved.

    Returns
    -------
    str
        The prefix to place before each transformed body line.
    """
    result: list[str] = []
    rest = prefix
    while rest:
        quote = _QUOTE_PREFIX.match(rest)
        if quote is not None:
            result.append(quote.group(0))
            rest = rest[quote.end() :]
            continue
        list_marker = _LIST_PREFIX.match(rest)
        if list_marker is not None:
            result.append(" " * list_marker.end())
            rest = rest[list_marker.end() :]
            continue
        break
    if not rest.strip():
        result.append(rest)
        return "".join(result)
    return f"{''.join(result)}{' ' * len(rest)}"


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


def _try_assign_version_at_path(
    document: TOMLDocument,
    path: tuple[str, ...],
    target_version: str,
) -> bool:
    """Attempt to assign version at the specified table path."""
    return bump_toml.assign_version(
        bump_toml.select_table(document, path), target_version
    )


def _update_single_dependency_section(
    document: TOMLDocument,
    section: str,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Update a single dependency section if it exists."""
    table = bump_toml.select_table(document, (section,))
    if table is None:
        return False
    return bump_toml.update_dependency_table(table, dependency_targets, target_version)


def update_toml_snippet_dependencies(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Update dependency sections in a TOML snippet document.

    Parameters
    ----------
    document
        A parsed TOML document to modify in place.
    dependency_targets
        Names of crates whose dependency entries should be updated.
    target_version
        The version string to apply to matching entries.

    Returns
    -------
    bool
        True if any dependency entries were updated.

    """
    if not dependency_targets:
        return False

    changed = False
    for section in bump_toml.DEPENDENCY_SECTIONS:
        if _update_single_dependency_section(
            document, section, dependency_targets, target_version
        ):
            changed = True
    return changed


def update_toml_snippet_versions(
    snippet: str,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> tuple[str, bool]:
    """Return a TOML snippet with dependency versions rewritten.

    Parameters
    ----------
    snippet
        A TOML snippet string to process.
    dependency_targets
        Names of crates whose dependency entries should be updated.
    target_version
        The version string to apply to matching entries.

    Returns
    -------
    tuple[str, bool]
        A tuple of (updated_snippet, changed) where changed indicates whether
        any modifications were made.

    """
    try:
        document = parse_toml(snippet)
    except TOMLKitError:
        return snippet, False

    changed = False
    if _try_assign_version_at_path(document, ("package",), target_version):
        changed = True
    if _try_assign_version_at_path(document, ("workspace", "package"), target_version):
        changed = True
    if update_toml_snippet_dependencies(document, dependency_targets, target_version):
        changed = True

    if not changed:
        return snippet, False

    suffix_match = re.search(r"((?:\r?\n)*)$", snippet)
    newline_suffix = suffix_match.group(1) if suffix_match else ""
    rendered = document.as_string().rstrip("\r\n")
    return f"{rendered}{newline_suffix}", True
