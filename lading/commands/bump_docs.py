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
import typing as typ

from lading.commands.bump_docs_fences import (
    rewrite_markdown_toml_fences,
    update_toml_snippet_dependencies,
    update_toml_snippet_versions,
)
from lading.utils.confinement import (
    ConfinedPath,
    GlobOptions,
    MatchExpectation,
    UnmatchedPolicy,
    expand_confined_glob,
)

__all__ = [
    "DocumentationUpdateRequest",
    "prepare_documentation_updates",
    "rewrite_markdown_toml_fences",
    "update_toml_snippet_dependencies",
    "update_toml_snippet_versions",
]

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from pathlib import Path

    from lading import config as config_module


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
            seen.add(match.parts)
            text = request.read_source(match)
            updated_text, changed = rewrite_markdown_toml_fences(
                text, dependency_targets, request.target_version
            )
            if not changed:
                continue
            updates.append((match, updated_text))
    return tuple(updates)
