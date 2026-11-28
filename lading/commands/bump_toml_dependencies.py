"""Cargo dependency-section version rewriting for manifests and snippets.

These helpers update requirement values in ``dependencies``,
``dev-dependencies``, and ``build-dependencies`` tables. Manifest rewrites and
Markdown snippet rewrites both use them. Table selection, version fields, and
manifest file helpers live in :mod:`lading.commands.bump_toml`.
"""

import collections.abc as cabc
import re
import typing as typ
from contextlib import suppress

from tomlkit import string
from tomlkit.items import InlineTable, Item, Table

from lading.commands.bump_toml import TableLike, select_table, value_as_string

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from tomlkit.toml_document import TOMLDocument

NON_DIGIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^([^\d]*)")

# Canonical Cargo dependency-section vocabulary (issue #103). Every module
# that iterates or maps dependency sections must reference this constant
# rather than re-declaring the literals. Order is significant: the elements
# are the normal, dev, and build sections respectively, and ``bump`` unpacks
# them positionally into ``_NORMAL_SECTION``/``_DEV_SECTION``/``_BUILD_SECTION``.
DEPENDENCY_SECTIONS: typ.Final[tuple[str, str, str]] = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


def compose_requirement(existing: str, target_version: str) -> str:
    """Prefix ``target_version`` with any non-numeric operator from ``existing``.

    Parameters
    ----------
    existing : str
        The current requirement string, possibly carrying an operator prefix
        such as ``^`` or ``~``.
    target_version : str
        The bare target version to apply.

    Returns
    -------
    str
        ``target_version`` carrying the operator prefix of ``existing`` when
        one is present.

    Examples
    --------
    >>> compose_requirement("^1.0.0", "2.0.0")
    '^2.0.0'
    >>> compose_requirement("1.0.0", "2.0.0")
    '2.0.0'
    """
    match = NON_DIGIT_PREFIX.match(existing)
    if not match:
        return target_version
    prefix = match.group(1)
    if not prefix or prefix == existing:
        return target_version
    return f"{prefix}{target_version}"


def prepare_version_replacement(
    value: object,
    target_version: str,
    *,
    compose: cabc.Callable[[str, str], str] = compose_requirement,
) -> Item | None:
    """Return an updated requirement value when ``value`` stores a string.

    Parameters
    ----------
    value : object
        The current requirement value, either a tomlkit
        :class:`~tomlkit.items.Item` or a raw Python value.
    target_version : str
        The target version to apply.
    compose : Callable[[str, str], str], optional
        Requirement composer used to build the replacement. Defaults to
        :func:`compose_requirement`, which replaces the complete requirement.
        Callers that must retain later comparators can supply their own.

    Returns
    -------
    Item | None
        The replacement item, or ``None`` when ``value`` is not a string or
        already matches the target requirement.

    Examples
    --------
    >>> value_as_string(prepare_version_replacement("^1.0.0", "2.0.0"))
    '^2.0.0'
    >>> prepare_version_replacement("^2.0.0", "2.0.0") is None
    True
    """
    current = value_as_string(value)
    if current is None:
        return None
    replacement_text = compose(current, target_version)
    if replacement_text == current:
        return None
    replacement = string(replacement_text)
    if isinstance(value, Item):
        with suppress(AttributeError):  # Preserve inline comments and whitespace trivia
            replacement._trivia = value._trivia
    return replacement


def assign_dependency_version_field(
    container: InlineTable | Table,
    target_version: str,
) -> bool:
    """Update the ``version`` key of ``container`` if present.

    Parameters
    ----------
    container : InlineTable | Table
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
    replacement = prepare_version_replacement(current, target_version)
    if replacement is None:
        return False
    container["version"] = replacement
    return True


def update_dependency_entry(
    container: TableLike,
    key: str,
    entry: object,
    target_version: str,
) -> bool:
    """Update a dependency entry with ``target_version`` if it records a version.

    Parameters
    ----------
    container : TableLike
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
    if isinstance(entry, InlineTable | Table):
        return assign_dependency_version_field(entry, target_version)
    replacement = prepare_version_replacement(entry, target_version)
    if replacement is None:
        return False
    container[key] = replacement  # type: ignore[index]  # OutOfOrderTableProxy supports item assignment
    return True


def update_dependency_table(
    table: TableLike,
    dependency_names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency requirements within ``table`` for ``dependency_names``.

    Parameters
    ----------
    table : TableLike
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
    document: TOMLDocument | TableLike,
    path: tuple[str, ...],
    names: cabc.Collection[str],
    target_version: str,
) -> bool:
    r"""Update dependency versions in the table at ``path``.

    Parameters
    ----------
    document : TOMLDocument | TableLike
        The parsed TOML manifest document, or a table to descend from.
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
    table = select_table(document, path)
    if table is None:
        return False
    return update_dependency_table(table, names, target_version)


def update_dependency_sections(
    document: TOMLDocument | TableLike,
    dependency_sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
    *,
    include_workspace_sections: bool = False,
) -> bool:
    r"""Apply ``target_version`` to dependency entries for the provided sections.

    Parameters
    ----------
    document : TOMLDocument | TableLike
        The parsed TOML manifest document, or a table to descend from.
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
    changed = _update_named_sections(document, dependency_sections, target_version)
    if include_workspace_sections:
        changed |= _update_named_sections(
            document, dependency_sections, target_version, prefix=("workspace",)
        )
    return changed


def _update_named_sections(
    document: TOMLDocument | TableLike,
    sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
    prefix: tuple[str, ...] = (),
) -> bool:
    """Apply ``target_version`` under ``prefix`` for each named section."""
    changed = False
    for section, names in sections.items():
        if not names:
            continue
        changed |= update_section(document, (*prefix, section), names, target_version)
    return changed


def update_workspace_dependency_sections(
    document: TOMLDocument,
    workspace_sections: cabc.Mapping[str, cabc.Collection[str]],
    target_version: str,
) -> bool:
    """Apply ``target_version`` to ``[workspace.<section>]`` dependency entries.

    Parameters
    ----------
    document : TOMLDocument
        The parsed TOML manifest document.
    workspace_sections : cabc.Mapping[str, cabc.Collection[str]]
        Mapping of section names to the workspace dependency keys to update.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        True if any version entries were changed.
    """
    return _update_named_sections(
        document, workspace_sections, target_version, prefix=("workspace",)
    )
