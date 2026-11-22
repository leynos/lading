"""Rewrite TOML values in documentation snippets.

:mod:`lading.commands.bump_docs` parses Markdown fences and delegates their
TOML transformations here. This module updates package versions and Cargo
dependency entries while preserving snippet newline conventions. The original
``bump_docs`` module re-exports these functions for its existing callers.
"""

import collections.abc as cabc
import re
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading.commands import (
    bump_toml,
    bump_toml_dependencies,
)
from lading.commands.bump_toml_dependency_updates import (
    update_matching_dependency_section,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


def _try_assign_version_at_path(
    document: TOMLDocument,
    path: tuple[str, ...],
    target_version: str,
) -> bool:
    """Assign a package version when the selected table exists."""
    return bump_toml.assign_version(
        bump_toml.select_table(document, path), target_version
    )


def update_toml_snippet_dependencies(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency sections in a TOML snippet document.

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
        ``True`` if any dependency entries were updated.

    Examples
    --------
    >>> document = parse_toml('[dependencies]\nalpha = "0.1.0"')
    >>> update_toml_snippet_dependencies(document, {"alpha"}, "0.2.0")
    True
    >>> document["dependencies"]["alpha"]
    '0.2.0'
    """
    if not dependency_targets:
        return False

    changed = False
    for section in bump_toml_dependencies.DEPENDENCY_SECTIONS:
        if update_matching_dependency_section(
            document,
            (section,),
            dependency_targets,
            target_version,
        ):
            changed = True
    return changed


def update_toml_snippet_versions(
    snippet: str,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> tuple[str, bool]:
    r"""Return a TOML snippet with package and dependency versions rewritten.

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
        The rewritten snippet and whether any value changed. Malformed TOML
        returns the original snippet with ``False``.

    Examples
    --------
    >>> snippet = '[package]\nversion = "0.1.0"'
    >>> updated, changed = update_toml_snippet_versions(
    ...     snippet, set(), "0.2.0"
    ... )
    >>> updated.splitlines(), changed
    (['[package]', 'version = "0.2.0"'], True)
    """
    try:
        document = parse_toml(snippet)
    except TOMLKitError:
        return snippet, False

    changed = _try_assign_version_at_path(document, ("package",), target_version)
    changed |= _try_assign_version_at_path(
        document, ("workspace", "package"), target_version
    )
    changed |= update_toml_snippet_dependencies(
        document, dependency_targets, target_version
    )
    if not changed:
        return snippet, False

    suffix_match = re.search(r"((?:\r?\n)*)$", snippet)
    newline_suffix = suffix_match.group(1) if suffix_match else ""
    rendered = document.as_string().rstrip("\r\n")
    return f"{rendered}{newline_suffix}", True
