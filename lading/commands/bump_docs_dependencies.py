"""Dependency-section rewriting for documentation TOML fences.

The documentation pass covers the same dependency locations as the manifest
pass, so a fence in a Markdown file and the `Cargo.toml` it documents cannot
drift apart. A rewrite visits:

- the top-level ``[dependencies]``, ``[dev-dependencies]``, and
  ``[build-dependencies]`` tables;
- their ``[workspace.*]`` counterparts, such as ``[workspace.dependencies]``;
- every ``[target.<spec>.<section>]`` table whose selector sits directly under
  ``target``, such as ``[target.'cfg(unix)'.dependencies]``.

Only the dependency keys named by the caller are rewritten, and only their
``version`` requirement; other keys and other crates are left untouched. Three
shapes are skipped without error because they cannot be updated in place:

- ``target`` given as an inline table (``target = { ... }``) or as a scalar,
  which is not a table the traversal can descend;
- a selector whose value is an inline table rather than a standard table;
- a dependency table nested more deeply than one selector level, such as
  ``[target.'cfg(unix)'.nested.dependencies]``, whose Cargo meaning this module
  does not model.

A dependency entry that tomlkit parses as an ``OutOfOrderTableProxy`` is also
skipped: the shared updater recognises only ``Table`` and ``InlineTable``
entries. That limitation is shared with the manifest pass.
"""

import collections.abc as cabc
import typing as typ

from lading.commands import bump_toml

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from tomlkit.toml_document import TOMLDocument

__all__ = ["update_toml_snippet_dependencies"]


def _dependency_sections(
    dependency_targets: cabc.Collection[str],
) -> dict[str, cabc.Collection[str]]:
    """Map every canonical section name to ``dependency_targets``."""
    return dict.fromkeys(bump_toml.DEPENDENCY_SECTIONS, dependency_targets)


def _update_target_tables(
    document: TOMLDocument,
    sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
) -> bool:
    """Rewrite dependency entries under each immediate ``target`` selector."""
    target_root = bump_toml.select_table(document, ("target",))
    if target_root is None:
        return False
    changed = False
    for selector in target_root:
        table = bump_toml.select_table(target_root, (selector,))
        if table is None:
            continue
        changed |= bump_toml.update_dependency_sections(table, sections, target_version)
    return changed


def update_toml_snippet_dependencies(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency sections in a TOML snippet document.

    The rewrite visits the top-level and ``[workspace.*]`` dependency sections,
    then each ``[target.<spec>.<section>]`` table. Locations the traversal
    cannot reach are skipped silently; see the module docstring for the list.

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

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml("[workspace.dependencies]\nalpha = '1.0.0'\n")
    >>> update_toml_snippet_dependencies(document, {"alpha"}, "2.0.0")
    True
    >>> document["workspace"]["dependencies"]["alpha"]
    '2.0.0'
    """
    if not dependency_targets:
        return False

    sections = _dependency_sections(dependency_targets)
    changed = bump_toml.update_dependency_sections(
        document,
        sections,
        target_version,
        include_workspace_sections=True,
    )
    # Evaluated even when the first pass already reported a change, so a
    # short-circuit ``or`` would hide updates (and regressions) below.
    changed |= _update_target_tables(document, sections, target_version)
    return changed
