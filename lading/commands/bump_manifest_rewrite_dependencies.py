"""Match workspace crates in dependency sections of Cargo manifests.

This module owns traversal of direct, workspace, and target dependency tables.
The bump planner uses :func:`rewrite_dependency_requirements` after selecting
an allowlisted non-member manifest.
"""

import collections.abc as cabc
import typing as typ

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Item, Table

from lading.commands import bump_toml

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


def rewrite_dependency_requirements(
    document: TOMLDocument,
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update matching dependency requirements in a Cargo manifest.

    Parameters
    ----------
    document : TOMLDocument
        Parsed Cargo manifest whose dependency tables may be updated.
    updated_crate_names : Collection[str]
        Workspace package names selected for the current bump.
    target_version : str
        Version to assign while preserving each requirement's operator.

    Returns
    -------
    bool
        Whether at least one dependency requirement changed.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml('[dependencies]\nalpha = "^1.0.0"')
    >>> rewrite_dependency_requirements(document, {"alpha"}, "2.0.0")
    True
    >>> document.as_string()
    '[dependencies]\nalpha = "^2.0.0"'
    """
    changed = False
    for section in bump_toml.DEPENDENCY_SECTIONS:
        changed |= _rewrite_dependency_section(
            document, (section,), updated_crate_names, target_version
        )
        changed |= _rewrite_dependency_section(
            document,
            ("workspace", section),
            updated_crate_names,
            target_version,
        )
    changed |= _rewrite_target_dependencies(
        document, updated_crate_names, target_version
    )
    return changed


def _rewrite_target_dependencies(
    document: TOMLDocument,
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Rewrite matching dependency sections under each target selector."""
    target_table = bump_toml.select_table(document, ("target",))
    if target_table is None:
        return False
    changed = False
    for selector in tuple(target_table):
        changed |= _rewrite_target_selector(
            document, selector, updated_crate_names, target_version
        )
    return changed


def _rewrite_target_selector(
    document: TOMLDocument,
    selector: str,
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Rewrite each supported dependency section under one target selector."""
    changed = False
    for section in bump_toml.DEPENDENCY_SECTIONS:
        changed |= _rewrite_dependency_section(
            document,
            ("target", selector, section),
            updated_crate_names,
            target_version,
        )
    return changed


def _rewrite_dependency_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Match dependency keys and package aliases, then reuse section updates."""
    table = bump_toml.select_table(document, path)
    if table is None:
        return False
    matching_names = {
        name
        for name, entry in tuple(table.items())
        if _is_matching_dependency(name, entry, updated_crate_names)
    }
    if not matching_names:
        return False
    return bump_toml.update_section(document, path, matching_names, target_version)


def _is_matching_dependency(
    name: str,
    entry: object,
    updated_crate_names: cabc.Collection[str],
) -> bool:
    """Return whether a dependency key or package alias is safe to update."""
    match entry:
        case (InlineTable() | Table() | OutOfOrderTableProxy()) as table:
            if _inherits_workspace_version(table):
                return False
            package_item = table.get("package")
            if package_item is None:
                return name in updated_crate_names
            package_name = bump_toml.value_as_string(package_item)
            return package_name is not None and package_name in updated_crate_names
        case _:
            return (
                name in updated_crate_names
                and bump_toml.value_as_string(entry) is not None
            )


def _inherits_workspace_version(
    entry: InlineTable | Table | OutOfOrderTableProxy,
) -> bool:
    """Return whether a dependency table sets ``workspace = true``."""
    match entry.get("workspace"):
        case Item() as item:
            return item.value is True
        case flag:
            return flag is True
