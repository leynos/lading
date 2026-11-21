r"""Update dependency tables in parsed TOML manifests.

This module owns dependency-entry traversal for Cargo manifests and TOML
snippets. It delegates string replacement and table selection to
:mod:`lading.commands.bump_toml` so those operations keep one implementation.

Examples
--------
>>> from tomlkit import parse as parse_toml
>>> from lading.commands.bump_toml_dependencies import update_dependency_sections
>>> document = parse_toml('[dependencies]\nalpha = "^1.0.0"')
>>> update_dependency_sections(document, {"dependencies": {"alpha"}}, "2.0.0")
True
>>> document["dependencies"]["alpha"]
'^2.0.0'
"""

import collections.abc as cabc
import typing as typ

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Item, Table

from lading.commands import bump_toml
from lading.commands.bump_requirement_bounds import (
    RequirementBoundWarning,
    requirement_bound_warning,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

_TableLike = Table | InlineTable | OutOfOrderTableProxy
_TABLE_LIKE_TYPES: typ.Final[
    tuple[type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...]
] = (
    Table,
    InlineTable,
    OutOfOrderTableProxy,
)

# Canonical Cargo dependency-section vocabulary (issue #103). The order is
# normal, dev, then build dependencies.
DEPENDENCY_SECTIONS: typ.Final[tuple[str, str, str]] = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


def assign_dependency_version_field(
    container: _TableLike,
    target_version: str,
) -> bool:
    """Update the ``version`` key of ``container`` if present.

    Parameters
    ----------
    container : _TableLike
        The dependency table whose ``version`` key is updated in place.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        ``True`` when the ``version`` key was updated.

    Examples
    --------
    >>> from tomlkit import inline_table
    >>> dep = inline_table()
    >>> dep["version"] = "1.0.0"
    >>> assign_dependency_version_field(dep, "2.0.0")
    True
    >>> dep["version"]
    '2.0.0'
    """
    current = container.get("version")
    replacement = bump_toml.prepare_version_replacement(current, target_version)
    if replacement is None:
        return False
    container["version"] = replacement
    return True


def update_dependency_entry(
    container: _TableLike,
    key: str,
    entry: object,
    target_version: str,
) -> bool:
    """Update a dependency entry with ``target_version`` if it records a version.

    Parameters
    ----------
    container : _TableLike
        The parent table holding ``key``; updated in place for scalar entries.
    key : str
        The dependency name identifying the entry within ``container``.
    entry : object
        The current entry value, either a nested table or a scalar requirement.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        ``True`` when the entry was updated.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> table = parse_toml('alpha = "1.0.0"')
    >>> update_dependency_entry(table, "alpha", table["alpha"], "2.0.0")
    True
    >>> table["alpha"]
    '2.0.0'
    """
    if isinstance(entry, _TABLE_LIKE_TYPES):
        return assign_dependency_version_field(entry, target_version)
    replacement = bump_toml.prepare_version_replacement(entry, target_version)
    if replacement is None:
        return False
    container[key] = replacement  # type: ignore[index]  # OutOfOrderTableProxy supports item assignment
    return True


def dependency_target_name(name: str, entry: object) -> str | None:
    """Return the package name represented by a dependency entry, if known.

    Parameters
    ----------
    name : str
        Dependency key from its containing Cargo section.
    entry : object
        Scalar requirement or TOML dependency table.

    Returns
    -------
    str or None
        Explicit ``package`` value when present, otherwise the dependency key
        for a TOML table or string requirement. Workspace-inherited tables and
        non-string scalar entries return ``None``.

    Examples
    --------
    >>> from tomlkit import parse
    >>> table = parse('renamed = { package = "alpha", version = "1.0" }')
    >>> dependency_target_name("renamed", table["renamed"])
    'alpha'
    """
    match entry:
        case (InlineTable() | Table() | OutOfOrderTableProxy()) as table:
            if _inherits_workspace_version(table):
                return None
            package_item = table.get("package")
            if package_item is None:
                return name
            return bump_toml.value_as_string(package_item)
        case _:
            return name if bump_toml.value_as_string(entry) is not None else None


def _inherits_workspace_version(
    entry: InlineTable | Table | OutOfOrderTableProxy,
) -> bool:
    """Return whether a dependency table inherits its version from a workspace."""
    match entry.get("workspace"):
        case Item() as item:
            return item.value is True
        case flag:
            return flag is True


def collect_dependency_bound_warnings(
    table: _TableLike,
    dependency_names: cabc.Collection[str],
    target_version: str,
) -> tuple[RequirementBoundWarning, ...]:
    r"""Return comparator warnings for dependency entries selected in ``table``.

    Parameters
    ----------
    table : _TableLike
        Dependency table to inspect without mutating it.
    dependency_names : Collection[str]
        Names whose dependency requirements will be rewritten.
    target_version : str
        Full version requested for this bump.

    Returns
    -------
    tuple[RequirementBoundWarning, ...]
        Structured warnings for preserved comparators that reject the target.

    Examples
    --------
    >>> from tomlkit import parse
    >>> table = parse('[dependencies]\nalpha = ">=1.0.0, <2"')["dependencies"]
    >>> warnings = collect_dependency_bound_warnings(table, {"alpha"}, "2.0.0")
    >>> warnings[0].excluded_bounds
    ('<2',)
    """
    warnings: list[RequirementBoundWarning] = []
    for name in dependency_names:
        if name not in table:
            continue
        entry = table[name]
        if isinstance(entry, _TABLE_LIKE_TYPES):
            if _inherits_workspace_version(entry):
                continue
            current = bump_toml.value_as_string(entry.get("version"))
        else:
            current = bump_toml.value_as_string(entry)
        if current is None:
            continue
        warning = requirement_bound_warning(current, target_version)
        if warning is not None:
            warnings.append(warning)
    return tuple(warnings)


def collect_dependency_section_warnings(
    document: TOMLDocument,
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
    *,
    include_workspace_sections: bool = False,
) -> tuple[RequirementBoundWarning, ...]:
    """Collect bound warnings for selected sections without mutating TOML.

    Parameters
    ----------
    document : TOMLDocument
        Manifest document whose selected dependency tables are inspected.
    dependency_sections : Mapping[str, Collection[str]]
        Section names and dependency keys scheduled for rewriting.
    target_version : str
        Full version requested for this bump.
    include_workspace_sections : bool, optional
        Also inspect matching ``[workspace.<section>]`` tables.

    Returns
    -------
    tuple[RequirementBoundWarning, ...]
        Warning data for selected comparators that reject ``target_version``.
    """
    warnings: list[RequirementBoundWarning] = []
    for section, names in dependency_sections.items():
        if not names:
            continue
        for path in _selected_dependency_section_paths(
            section, include_workspace_sections=include_workspace_sections
        ):
            table = bump_toml.select_table(document, path)
            if table is not None:
                warnings.extend(
                    collect_dependency_bound_warnings(table, names, target_version)
                )
    return tuple(warnings)


def _selected_dependency_section_paths(
    section: str,
    *,
    include_workspace_sections: bool,
) -> tuple[tuple[str, ...], ...]:
    """Return direct and optional workspace paths for one section."""
    direct = (section,)
    if not include_workspace_sections:
        return (direct,)
    return direct, ("workspace", section)


def update_dependency_table(
    table: _TableLike,
    dependency_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency requirements within ``table`` for ``dependency_names``.

    Parameters
    ----------
    table : _TableLike
        The dependency table whose entries are updated in place.
    dependency_names : cabc.Collection[str]
        Names of the dependencies to update within ``table``.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        ``True`` when at least one dependency requirement was updated.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> table = parse_toml('alpha = "1.0.0"\nbeta = "1.0.0"\n')
    >>> update_dependency_table(table, {"alpha"}, "2.0.0")
    True
    >>> table["alpha"], table["beta"]
    ('2.0.0', '1.0.0')
    """
    changed = False
    for name in dependency_names:
        if name not in table:
            continue
        entry = table[name]  # OutOfOrderTableProxy supports indexing
        if update_dependency_entry(table, name, entry, target_version):
            changed = True
    return changed


def update_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency versions in the table at ``path``.

    Parameters
    ----------
    document : TOMLDocument
        The parsed TOML manifest document.
    path : tuple[str, ...]
        Tuple of keys identifying the table path (e.g., ``("workspace",
        "dependencies")``).
    names : cabc.Collection[str]
        Collection of dependency names to update.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        True if any version entries were changed.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml('[dependencies]\nalpha = "0.1.0"\n')
    >>> update_section(document, ("dependencies",), {"alpha"}, "0.2.0")
    True
    """
    table = bump_toml.select_table(document, path)
    if table is None:
        return False
    return update_dependency_table(table, names, target_version)


def update_dependency_sections(
    document: TOMLDocument,
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
    *,
    include_workspace_sections: bool = False,
) -> bool:
    r"""Apply ``target_version`` to dependency entries for the provided sections.

    Parameters
    ----------
    document : TOMLDocument
        The parsed TOML manifest document.
    dependency_sections : cabc.Mapping[str, cabc.Collection[str]]
        Mapping of section names to crate names to update.
    target_version : str
        The target version to apply.
    include_workspace_sections : bool, optional
        When True, also update entries in ``[workspace.<section>]`` tables
        (e.g., ``[workspace.dependencies]``).

    Returns
    -------
    bool
        True if any version entries were changed.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml('[dev-dependencies]\nalpha = "0.1.0"\n')
    >>> sections = {"dev-dependencies": {"alpha"}}
    >>> update_dependency_sections(document, sections, "0.2.0")
    True
    """
    changed = False
    for section, names in dependency_sections.items():
        if not names:
            continue
        changed |= update_section(document, (section,), names, target_version)
        if include_workspace_sections:
            changed |= update_section(
                document, ("workspace", section), names, target_version
            )
    return changed
