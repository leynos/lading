"""Documentation processing utilities for version bumping.

This module resolves and updates Markdown documentation files. It scans fenced
TOML code blocks (e.g., Cargo.toml snippets in README files) and delegates TOML
changes to :mod:`lading.commands.bump_docs_toml`. Its public surface includes
``resolve_documentation_targets``, ``update_documentation_files``,
``rewrite_markdown_toml_fences``, and ``update_toml_snippet_versions``.

Examples
--------
>>> import tempfile
>>> from pathlib import Path
>>> from lading import config
>>> from lading.commands import bump_docs
>>> with tempfile.TemporaryDirectory() as directory:
...     workspace = Path(directory)
...     _ = (workspace / "README.md").write_text("", encoding="utf-8")
...     documentation = config.DocumentationConfig(globs=("README.md",))
...     targets = bump_docs.resolve_documentation_targets(
...         workspace, documentation
...     )
...     [path.name for path in targets.paths]
['README.md']

``update_documentation_files(targets, "1.2.0", {"my-crate"}, dry_run=False)``
then rewrites the version entries in those files and returns the ones it
changed. The targets retain their workspace root so direct callers receive the
same containment checks as targets collected from configuration.
"""

import collections.abc as cabc
import dataclasses as dc
import logging
import re
import typing as typ
from pathlib import Path

from markdown_it import MarkdownIt

from lading.commands import bump_toml_files, manifest_paths
from lading.commands.bump_docs_toml import (
    update_toml_snippet_dependencies as update_toml_snippet_dependencies,
)
from lading.commands.bump_docs_toml import (
    update_toml_snippet_versions as update_toml_snippet_versions,
)
from lading.exceptions import LadingError

_LOGGER = logging.getLogger(__name__)

if typ.TYPE_CHECKING:
    from markdown_it.token import Token

    from lading import config as config_module


@dc.dataclass(frozen=True, slots=True)
class DocumentationTargets:
    """Resolved documentation paths paired with their owning workspace.

    Parameters
    ----------
    workspace_root
        Workspace root against which every path must be checked.
    paths
        Final documentation paths, in resolver order. Relative paths are
        interpreted from the current working directory and should normally be
        assembled as ``workspace_root / relative_path``.

    Examples
    --------
    >>> from pathlib import Path
    >>> targets = DocumentationTargets(Path("/workspace"), ())
    >>> targets.paths
    ()
    """

    workspace_root: Path
    paths: tuple[Path, ...]


def resolve_documentation_targets(
    workspace_root: Path, documentation: config_module.DocumentationConfig
) -> DocumentationTargets:
    """Return documentation files that should be scanned for version updates.

    Parameters
    ----------
    workspace_root
        The root directory of the workspace to search within.
    documentation
        Configuration specifying glob patterns for documentation files.

    Returns
    -------
    DocumentationTargets
        Workspace-bound paths matching the configured patterns. The root is
        retained for containment checks before updates.

    """
    resolved_root = workspace_root.resolve()
    patterns = documentation.globs
    if not patterns:
        return DocumentationTargets(resolved_root, ())

    resolved: set[Path] = set()
    for pattern in patterns:
        for candidate in resolved_root.glob(pattern):
            safe_candidate = manifest_paths.resolve_workspace_path(
                resolved_root,
                candidate,
                error_type=LadingError,
                label="Documentation",
            )
            if safe_candidate.is_file():
                resolved.add(safe_candidate)
    return DocumentationTargets(resolved_root, tuple(resolved))


def update_documentation_files(
    documentation_targets: DocumentationTargets,
    target_version: str,
    updated_crates: cabc.Collection[str],
    *,
    dry_run: bool,
) -> set[Path]:
    """Rewrite documentation TOML fences that mention workspace crates.

    Parameters
    ----------
    documentation_targets
        Resolved documentation paths paired with the workspace boundary.
    target_version
        The version string to apply to matching entries.
    updated_crates
        Names of crates whose dependency entries should be updated.
    dry_run
        When True, report changes without modifying files.

    Returns
    -------
    set[Path]
        Paths to documentation files that were (or would be) modified.

    """
    changed: set[Path] = set()
    dependency_targets = {name for name in updated_crates if name}
    for candidate in documentation_targets.paths:
        doc_path = manifest_paths.resolve_workspace_path(
            documentation_targets.workspace_root,
            candidate.resolve(),
            error_type=LadingError,
            label="Documentation",
        )
        try:
            original_text = doc_path.read_text(encoding="utf-8")
        except OSError as exc:
            _LOGGER.warning("Skipping %s: %s", doc_path, exc)
            continue
        updated_text, snippet_changed = rewrite_markdown_toml_fences(
            original_text, dependency_targets, target_version
        )
        if not snippet_changed:
            continue
        changed.add(doc_path)
        if not dry_run:
            bump_toml_files.write_atomic_text(doc_path, updated_text)
    return changed


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
