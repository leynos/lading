"""TOML snippet version policy for documentation version bumps.

The functions here parse each fenced TOML block and decide which version
references to rewrite. Markdown fence discovery and rendering live in
:mod:`lading.commands.bump_docs_markdown`; filesystem discovery lives in
:mod:`lading.commands.bump_docs`.

Snippet rewriting is narrower than manifest rewriting. A fence's
``[package].version`` and ``[workspace.package].version`` are replaced only
when the fence's ``[package].name`` names a bumped crate and the version is an
existing string. The pass never inserts a version key, never re-versions an
unrelated example manifest, and never replaces ``version.workspace = true``.
"""

import collections.abc as cabc
import re
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import bump_toml
from lading.commands.bump_docs_dependencies import (
    update_toml_snippet_dependencies as update_toml_snippet_dependencies,
)
from lading.commands.bump_docs_markdown import replace_markdown_fences

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from tomlkit.toml_document import TOMLDocument

__all__ = [
    "rewrite_markdown_toml_fences",
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


def _names_bumped_crate(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
) -> bool:
    """Return whether ``[package].name`` names one of ``dependency_targets``."""
    package = bump_toml.select_table(document, ("package",))
    if package is None:
        return False
    name = bump_toml.value_as_string(package.get("name"))
    return name is not None and name in dependency_targets


def _update_snippet_package_versions(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Apply the snippet-scoped package version policy.

    A fence only describes a bumped workspace crate when its ``[package].name``
    names one of the bumped crates. Only then are existing string versions
    replaced; ``[workspace.package]`` follows the same gate because a fence
    without a package name is not evidence of this workspace's root manifest.

    Returns
    -------
    bool
        ``True`` when a package or workspace-package version was replaced.
    """
    if not _names_bumped_crate(document, dependency_targets):
        return False
    changed = bump_toml.replace_string_version(
        bump_toml.select_table(document, ("package",)), target_version
    )
    changed |= bump_toml.replace_string_version(
        bump_toml.select_table(document, ("workspace", "package")), target_version
    )
    return changed


def update_toml_snippet_versions(
    snippet: str,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> tuple[str, bool]:
    r"""Return a TOML snippet with eligible versions rewritten.

    The snippet-scoped policy is deliberately narrower than the manifest
    update: a package or workspace-package version is replaced only when it is
    an existing string and the snippet's ``[package].name`` names a bumped
    crate. A fence that documents metadata, an unrelated application, or
    ``version.workspace = true`` is returned unchanged. Dependency
    requirements for the bumped crates are rewritten independently.

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

    Examples
    --------
    >>> update_toml_snippet_versions(
    ...     '[package]\nname = "demo"\nversion = "0.1.0"\n',
    ...     {"demo"},
    ...     "1.0.0",
    ... )[1]
    True
    >>> update_toml_snippet_versions(
    ...     '[package.metadata.tool]\nmode = "warn"\n', {"demo"}, "1.0.0"
    ... )[1]
    False

    """
    try:
        document = parse_toml(snippet)
    except TOMLKitError:
        return snippet, False

    changed = _update_snippet_package_versions(
        document, dependency_targets, target_version
    )
    if update_toml_snippet_dependencies(document, dependency_targets, target_version):
        changed = True

    if not changed:
        return snippet, False

    suffix_match = re.search(r"((?:\r?\n)*)$", snippet)
    newline_suffix = suffix_match.group(1) if suffix_match else ""
    rendered = document.as_string().rstrip("\r\n")
    return f"{rendered}{newline_suffix}", True
