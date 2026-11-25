"""Log comparator warnings and apply selected TOML dependency updates.

This command-layer adapter combines read-only warning collection from
:mod:`lading.commands.bump_toml_dependency_warnings` with mutation from
:mod:`lading.commands.bump_toml_dependencies`. Member-manifest, documentation
snippet, and configured non-member adapters use it so each reports warnings
before updating the corresponding dependency entries.
"""

import collections.abc as cabc
import logging
import typing as typ

from lading.commands import (
    bump_toml,
    bump_toml_dependencies,
    bump_toml_dependency_warnings,
)
from lading.commands.bump_requirement_bounds import (
    RequirementBoundWarning,
    format_requirement_bound_warning,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from lading.commands.bump_toml_types import _TableLike

_LOGGER = logging.getLogger(__name__)


def update_dependency_table_with_warnings(
    table: _TableLike,
    dependency_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Log excluding bounds, then update selected entries in ``table``.

    Parameters
    ----------
    table : _TableLike
        Dependency table whose selected requirements are updated in place.
    dependency_names : Collection[str]
        Dependency keys to update.
    target_version : str
        Version to assign while preserving each requirement's operator.

    Returns
    -------
    bool
        ``True`` when at least one dependency requirement changed.

    Examples
    --------
    >>> from tomlkit import parse
    >>> table = parse('[dependencies]\nalpha = "^1.0.0"')["dependencies"]
    >>> update_dependency_table_with_warnings(table, {"alpha"}, "2.0.0")
    True
    >>> str(table["alpha"])
    '^2.0.0'
    """
    warnings = bump_toml_dependency_warnings.collect_dependency_bound_warnings(
        table, dependency_names, target_version
    )
    _log_warnings(warnings)
    return bump_toml_dependencies.update_dependency_table(
        table, dependency_names, target_version
    )


def update_matching_dependency_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    """Match dependency keys and package aliases before updating a section.

    Parameters
    ----------
    document : TOMLDocument
        Parsed Cargo manifest to update.
    path : tuple[str, ...]
        Literal dependency-table path.
    updated_crate_names : Collection[str]
        Workspace package names selected for the current bump.
    target_version : str
        Version to assign while preserving each requirement's operator.

    Returns
    -------
    bool
        ``True`` when a matching dependency requirement changed.
    """
    table = bump_toml.select_table(document, path)
    if table is None:
        return False
    matching_names = {
        name
        for name, entry in tuple(table.items())
        if bump_toml_dependencies.dependency_target_name(name, entry)
        in updated_crate_names
    }
    if not matching_names:
        return False
    return update_dependency_table_with_warnings(table, matching_names, target_version)


def update_dependency_sections_with_warnings(
    document: TOMLDocument,
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
    *,
    section_scope: typ.Literal["direct", "workspace", "both"] = "direct",
) -> bool:
    """Log selected comparator warnings, then update matching sections.

    Parameters
    ----------
    document : TOMLDocument
        Parsed manifest to update.
    dependency_sections : Mapping[str, Collection[str]]
        Section names and dependency keys selected for rewriting.
    target_version : str
        Version to assign while preserving each requirement's operator.
    section_scope : Literal["direct", "workspace", "both"], optional
        Dependency table locations to update: direct sections, workspace
        sections, or both.

    Returns
    -------
    bool
        ``True`` when at least one requirement changed.
    """
    if section_scope == "direct":
        warnings = bump_toml_dependency_warnings.collect_dependency_section_warnings(
            document, dependency_sections, target_version
        )
        _log_warnings(warnings)
        return bump_toml_dependencies.update_dependency_sections(
            document, dependency_sections, target_version
        )

    changed = False
    for section, package_names in dependency_sections.items():
        if section_scope in {"direct", "both"}:
            changed |= update_matching_dependency_section(
                document, (section,), package_names, target_version
            )
        workspace_table = (
            bump_toml.select_table(document, ("workspace", section))
            if section_scope in {"workspace", "both"}
            else None
        )
        if workspace_table is not None:
            changed |= update_dependency_table_with_warnings(
                workspace_table, package_names, target_version
            )
    return changed


def _log_warnings(
    warnings: cabc.Iterable[RequirementBoundWarning],
) -> None:
    """Log each collected comparator warning once."""
    for warning in warnings:
        _LOGGER.warning(format_requirement_bound_warning(warning))
