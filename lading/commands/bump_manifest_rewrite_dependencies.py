"""Match workspace crates in dependency sections of Cargo TOML.

The planner rewrites direct, workspace, and target-specific dependency
requirements whose package name matches a bumped workspace crate. Each
requirement keeps its first comparator and any later bounds.
"""

import collections.abc as cabc
import logging
import typing as typ

from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Table

from lading.commands import bump_manifest_rewrite_bounds, bump_toml

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

type _TableLike = Table | InlineTable | OutOfOrderTableProxy

_TABLE_TYPES: typ.Final[
    tuple[type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...]
] = (Table, InlineTable, OutOfOrderTableProxy)

_log = logging.getLogger(__name__)


def rewrite_dependency_requirements(
    document: TOMLDocument,
    updated_crate_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update matching dependency requirements in a Cargo TOML document.

    Parameters
    ----------
    document : TOMLDocument
        Parsed Cargo TOML document whose dependency tables may be updated.
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
    names = frozenset(updated_crate_names)
    sections: list[tuple[str, ...]] = []
    for section in bump_toml.DEPENDENCY_SECTIONS:
        sections.append((section,))
        sections.append(("workspace", section))
    target_table = bump_toml.select_table(document, ("target",))
    if target_table is not None:
        for selector in tuple(target_table):
            sections.extend(
                ("target", selector, section)
                for section in bump_toml.DEPENDENCY_SECTIONS
            )
    changed = False
    for path in sections:
        changed |= _rewrite_dependency_section(document, path, names, target_version)
    return changed


def _rewrite_dependency_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    names: frozenset[str],
    target_version: str,
) -> bool:
    """Rewrite the selected entries of one dependency table."""
    table = bump_toml.select_table(document, path)
    if table is None:
        return False
    changed = False
    for key, entry in tuple(table.items()):
        if _dependency_package_name(key, entry) not in names:
            continue
        if _rewrite_dependency_entry(table, key, entry, target_version):
            changed = True
    return changed


def _dependency_package_name(key: str, entry: object) -> str:
    """Return the package name a dependency key or entry refers to."""
    if isinstance(entry, _TABLE_TYPES):
        declared = bump_toml.value_as_string(entry.get("package"))
        if declared is not None:
            return declared
    return key


def _rewrite_dependency_entry(
    table: _TableLike,
    key: str,
    entry: object,
    target_version: str,
) -> bool:
    """Rewrite one dependency entry, preserving its requirement bounds."""
    if isinstance(entry, _TABLE_TYPES):
        if entry.get("workspace") is True:
            return False
        value = entry.get("version")
        replacement = bump_toml.prepare_version_replacement(
            value,
            target_version,
            compose=compose_requirement,
        )
        if replacement is None:
            return False
        _warn_if_bounds_exclude_target(key, value, target_version)
        entry["version"] = replacement
        return True
    replacement = bump_toml.prepare_version_replacement(
        entry, target_version, compose=compose_requirement
    )
    if replacement is None:
        return False
    _warn_if_bounds_exclude_target(key, entry, target_version)
    table[key] = replacement  # type: ignore[index]  # table-like values support assignment
    return True


def _warn_if_bounds_exclude_target(
    dependency_name: str,
    value: object,
    target_version: str,
) -> None:
    """Log once when a rewritten or retained comparator excludes the target."""
    requirement = bump_toml.value_as_string(value)
    if requirement is None:
        return
    warning = bump_manifest_rewrite_bounds.requirement_bound_warning(
        requirement, target_version
    )
    if warning is None:
        return
    _log.warning(
        "%s",
        bump_manifest_rewrite_bounds.format_requirement_bound_warning(warning),
        extra={"dependency": dependency_name},
    )


def compose_requirement(existing: str, target_version: str) -> str:
    """Replace the first requirement version and retain later constraints.

    Parameters
    ----------
    existing : str
        The current requirement string, possibly carrying an operator prefix
        such as ``^`` or ``~`` and comma-separated additional constraints.
    target_version : str
        The bare target version to apply.

    Returns
    -------
    str
        ``target_version`` carrying the first requirement's operator, followed
        by any additional constraints from ``existing``.

    Examples
    --------
    >>> compose_requirement("^1.0.0", "2.0.0")
    '^2.0.0'
    >>> compose_requirement("1.0.0", "2.0.0")
    '2.0.0'
    >>> compose_requirement(">=0.6.0-beta4, <0.7", "0.6.0")
    '>=0.6.0, <0.7'
    """
    first_constraint, separator, remaining_constraints = existing.partition(",")
    trimmed_constraint = first_constraint.rstrip()
    trailing_whitespace = first_constraint[len(trimmed_constraint) :]
    replacement = bump_toml.compose_requirement(trimmed_constraint, target_version)
    return f"{replacement}{trailing_whitespace}{separator}{remaining_constraints}"
