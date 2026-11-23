r"""Collect Cargo requirement-bound warnings from TOML dependency tables.

:mod:`lading.commands.bump_toml_dependencies` owns dependency-entry updates.
This module inspects the same entries without mutation and gathers structured
warnings for the manifest, documentation, and configured-fixture command
layers to log before applying their updates.

Examples
--------
>>> from tomlkit import parse as parse_toml
>>> from lading.commands.bump_toml_dependency_warnings import (
...     collect_dependency_section_warnings,
... )
>>> doc = parse_toml('[dependencies]\nalpha = ">=1.0.0, <2"')
>>> warnings = collect_dependency_section_warnings(
...     doc, {"dependencies": {"alpha"}}, "2.0.0"
... )
>>> warnings[0].excluded_bounds
('<2',)
"""

import collections.abc as cabc
import typing as typ

from lading.commands import bump_toml
from lading.commands.bump_requirement_bounds import (
    RequirementBoundWarning,
    requirement_bound_warning,
)
from lading.commands.bump_toml_dependencies import _inherits_workspace_version
from lading.commands.bump_toml_types import _TABLE_LIKE_TYPES, _TableLike

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


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
    warnings = (
        _dependency_bound_warning(table, name, target_version)
        for name in dependency_names
    )
    return tuple(warning for warning in warnings if warning is not None)


def _dependency_bound_warning(
    table: _TableLike,
    name: str,
    target_version: str,
) -> RequirementBoundWarning | None:
    """Return bound warnings for one dependency if it has a requirement."""
    if name not in table:
        return None
    current = _current_dependency_requirement(table[name])
    if current is None:
        return None
    return requirement_bound_warning(current, target_version)


def _current_dependency_requirement(entry: object) -> str | None:
    """Return a version requirement unless the dependency inherits it."""
    if not isinstance(entry, _TABLE_LIKE_TYPES):
        return bump_toml.value_as_string(entry)
    if _inherits_workspace_version(entry):
        return None
    return bump_toml.value_as_string(entry.get("version"))


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
    warnings = (
        warning
        for section, names in dependency_sections.items()
        if names
        for warning in _selected_section_warnings(
            document,
            _selected_dependency_section_paths(
                section, include_workspace_sections=include_workspace_sections
            ),
            names,
            target_version,
        )
    )
    return tuple(warnings)


def _selected_section_warnings(
    document: TOMLDocument,
    paths: cabc.Sequence[tuple[str, ...]],
    names: cabc.Collection[str],
    target_version: str,
) -> tuple[RequirementBoundWarning, ...]:
    """Collect warnings from the existing dependency tables in ``paths``."""
    warnings = (
        warning
        for path in paths
        if (table := bump_toml.select_table(document, path)) is not None
        for warning in collect_dependency_bound_warnings(table, names, target_version)
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
