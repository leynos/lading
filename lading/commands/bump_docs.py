r"""Documentation processing utilities for version bumping.

This module provides utilities for updating version references within Markdown
documentation files. It scans fenced TOML code blocks (e.g., Cargo.toml
snippets in README files) and rewrites version entries to match the target
workspace version. The preparation surface is
``prepare_documentation_updates``; the rewriting helpers
``rewrite_markdown_toml_fences`` and ``update_toml_snippet_versions`` remain
independently testable.

Examples
--------
>>> import tempfile
>>> from pathlib import Path
>>> from lading import config
>>> from lading.commands import bump_docs
>>> with tempfile.TemporaryDirectory() as directory:
...     workspace = Path(directory)
...     _ = (workspace / "README.md").write_text(
...         '```toml\n[dependencies]\nmy-crate = "1.0.0"\n```\n',
...         encoding="utf-8",
...     )
...     documentation = config.DocumentationConfig(globs=("README.md",))
...     request = bump_docs.DocumentationUpdateRequest(
...         target_version="1.2.0",
...         updated_crates=frozenset({"my-crate"}),
...         read_source=lambda path: path.path.read_text(encoding="utf-8"),
...     )
...     updates = bump_docs.prepare_documentation_updates(
...         workspace, documentation, request
...     )
...     [path.relative.as_posix() for path, _content in updates]
['README.md']
"""

import collections.abc as cabc
import dataclasses as dc
import re
import typing as typ

from markdown_it import MarkdownIt
from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import bump_toml
from lading.utils.confinement import (
    ConfinedPath,
    GlobOptions,
    MatchExpectation,
    UnmatchedPolicy,
    expand_confined_glob,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from markdown_it.token import Token
    from tomlkit.toml_document import TOMLDocument

    from lading import config as config_module

else:  # pragma: no cover - provide runtime placeholders for type checking imports
    Token = TOMLDocument = typ.Any


@dc.dataclass(frozen=True, slots=True)
class DocumentationUpdateRequest:
    """Inputs that describe one documentation-update pass.

    Attributes
    ----------
    target_version : str
        Version to apply inside matching TOML fences.
    updated_crates : frozenset[str]
        Names of crates whose dependency entries should be updated.
    read_source : cabc.Callable[[ConfinedPath], str]
        Reader that honours earlier proposed content.
    """

    target_version: str
    updated_crates: frozenset[str]
    read_source: cabc.Callable[[ConfinedPath], str]


def prepare_documentation_updates(
    workspace_root: Path,
    documentation: config_module.DocumentationConfig,
    request: DocumentationUpdateRequest,
) -> tuple[tuple[ConfinedPath, str], ...]:
    """Return the documentation writes a bump should perform.

    Parameters
    ----------
    workspace_root : Path
        Canonical workspace root.
    documentation : config_module.DocumentationConfig
        Configured glob patterns.
    request : DocumentationUpdateRequest
        Target version, crate names, and the reader for selected files.

    Returns
    -------
    tuple[tuple[ConfinedPath, str], ...]
        Confined paths and their complete rewritten text, in expansion order.

    Raises
    ------
    UnsafeBumpTargetError
        If a pattern escapes the workspace or matches a symlink or special
        file.
    BumpTargetExaminationError
        If a directory or selected file cannot be examined.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by expand_confined_glob
    dependency_targets = {name for name in request.updated_crates if name}
    updates: list[tuple[ConfinedPath, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, pattern in enumerate(documentation.globs):
        matches = expand_confined_glob(
            workspace_root,
            pattern,
            key=f"bump.documentation.globs[{index}]",
            options=GlobOptions(
                expect=MatchExpectation.REGULAR_FILE,
                unmatched=UnmatchedPolicy.IGNORE,
            ),
        )
        for match in matches:
            if match.parts in seen:
                continue
            text = request.read_source(match)
            updated_text, changed = rewrite_markdown_toml_fences(
                text, dependency_targets, request.target_version
            )
            if not changed:
                continue
            seen.add(match.parts)
            updates.append((match, updated_text))
    return tuple(updates)


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
    lines = markdown_text.splitlines(keepends=True)
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
    start, _ = token.map
    fence_marker = token.markup or "```"
    indent = extract_fence_indent(lines[start], fence_marker)
    info = token.info or language
    original_body = token.content
    new_body = transform(original_body)
    suffix_match = re.search(r"(\r?\n+)$", original_body)
    suffix = suffix_match.group(1) if suffix_match else ""
    body_text = new_body.rstrip("\r\n") + suffix
    indented_body = "".join(
        f"{indent}{line}" for line in body_text.splitlines(keepends=True)
    )
    return f"{indent}{fence_marker}{info}\n{indented_body}{indent}{fence_marker}\n"


def extract_fence_indent(line: str, fence_marker: str) -> str:
    """Return indentation preceding ``fence_marker`` in ``line``.

    Parameters
    ----------
    line
        A line of text potentially containing a fence marker.
    fence_marker
        The fence marker string to search for (e.g., "```").

    Returns
    -------
    str
        The whitespace preceding the fence marker, or empty string if not found.

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
