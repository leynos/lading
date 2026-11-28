"""Dependency-section rewriting for documentation TOML fences.

A rewrite visits:

- the top-level ``[dependencies]``, ``[dev-dependencies]``, and
  ``[build-dependencies]`` tables;
- their ``[workspace.*]`` counterparts, such as ``[workspace.dependencies]``;
- every ``[target.<spec>.<section>]`` table whose selector sits directly under
  ``target``, such as ``[target.'cfg(unix)'.dependencies]``.

The first two groups match what the manifest pass rewrites. The target tables
are documentation-only: the manifest pass leaves them alone because Cargo
applies a target-specific requirement solely to the matching target.

A dependency entry is identified by its ``package`` field when that field is
present, and by its key when the field is absent, matching the manifest pass. A
present but empty or non-string ``package`` field yields no identity, so the
entry is skipped rather than matched on its key. Only an identified entry's
``version`` requirement is rewritten. Four shapes are skipped without error
because they cannot be updated in place:

- ``target`` given as an inline table (``target = { ... }``) or as a scalar,
  which is not a table the traversal can descend;
- a selector whose value is an inline table rather than a standard table;
- a dependency section written as an inline table, such as
  ``dependencies = { alpha = "1.0.0" }``; an inline dependency *entry* such as
  ``alpha = { version = "1.0.0" }`` is supported;
- a dependency table nested more deeply than one selector level, such as
  ``[target.'cfg(unix)'.nested.dependencies]``, whose Cargo meaning this module
  does not model.

A dependency entry that tomlkit parses as an ``OutOfOrderTableProxy`` is
supported: the shared entry dispatch rewrites its ``version`` field in place.
An out-of-order ``target`` parent is supported too.
"""

import collections.abc as cabc
import typing as typ

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Table

from lading.commands import bump_toml, bump_toml_dependencies

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from tomlkit.toml_document import TOMLDocument

__all__ = ["update_toml_snippet_dependencies"]


def _dependency_identity(key: str, entry: object) -> str | None:
    """Return an entry's package rename, or its key when it declares none.

    A ``package`` field that is present but empty or non-string is
    authoritative, so such an entry has no identity and yields ``None`` rather
    than falling back to the key.

    Parameters
    ----------
    key : str
        The dependency key as written in the TOML fence.
    entry : object
        The parsed dependency entry.

    Returns
    -------
    str | None
        The package the entry renames, its key when no ``package`` field is
        present, or ``None`` when the field is present but unusable.
    """
    if not isinstance(entry, InlineTable | Table | OutOfOrderTableProxy):
        return key
    if "package" not in entry:
        return key
    return bump_toml.value_as_string(entry.get("package")) or None


def _update_dependency_tables(
    document: TOMLDocument | Table | InlineTable | OutOfOrderTableProxy,
    prefix: tuple[str, ...],
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Rewrite identified entries in each canonical section under ``prefix``."""
    changed = False
    for section in bump_toml_dependencies.DEPENDENCY_SECTIONS:
        table = bump_toml.select_table(document, (*prefix, section))
        if table is None:
            continue
        keys = {
            key
            for key, entry in table.items()
            if _dependency_identity(key, entry) in dependency_targets
        }
        if keys and bump_toml_dependencies.update_dependency_table(
            table, keys, target_version
        ):
            changed = True
    return changed


def _update_target_tables(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Rewrite identified entries under each immediate ``target`` selector."""
    target_root = bump_toml.select_table(document, ("target",))
    if target_root is None:
        return False
    changed = False
    for selector in target_root:
        table = bump_toml.select_table(target_root, (selector,))
        if table is None:
            continue
        if _update_dependency_tables(table, (), dependency_targets, target_version):
            changed = True
    return changed


def update_toml_snippet_dependencies(
    document: TOMLDocument,
    dependency_targets: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency sections in a TOML snippet document.

    The rewrite visits the top-level and ``[workspace.*]`` dependency sections,
    then each ``[target.<spec>.<section>]`` table. Each entry is identified by
    its ``package`` field when that field is present, and by its key when the
    field is absent. Locations the traversal cannot reach are skipped silently;
    see the module docstring for the list.

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

    changed = _update_dependency_tables(
        document, (), dependency_targets, target_version
    )
    changed |= _update_dependency_tables(
        document, ("workspace",), dependency_targets, target_version
    )
    # Evaluated even when an earlier pass already reported a change, so a
    # short-circuit ``or`` would hide updates (and regressions) below.
    changed |= _update_target_tables(document, dependency_targets, target_version)
    return changed
