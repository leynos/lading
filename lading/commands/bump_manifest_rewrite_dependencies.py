"""Match workspace crates in dependency sections of Cargo manifests.

This module owns traversal of direct, workspace, and target dependency tables.
The bump planner uses :func:`rewrite_dependency_requirements` after selecting
an allowlisted non-member manifest.
"""

import collections.abc as cabc
import logging
import typing as typ

from lading.commands import bump_toml, bump_toml_dependencies
from lading.commands.bump_requirement_bounds import format_requirement_bound_warning

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

_log = logging.getLogger(__name__)


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
    for section in bump_toml_dependencies.DEPENDENCY_SECTIONS:
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
    for section in bump_toml_dependencies.DEPENDENCY_SECTIONS:
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
        if bump_toml_dependencies.dependency_target_name(name, entry)
        in updated_crate_names
    }
    if not matching_names:
        return False
    for warning in bump_toml_dependencies.collect_dependency_bound_warnings(
        table, matching_names, target_version
    ):
        _log.warning(format_requirement_bound_warning(warning))
    return bump_toml_dependencies.update_section(
        document, path, matching_names, target_version
    )
