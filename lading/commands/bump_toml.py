"""TOML manipulation utilities for version bumping."""

from __future__ import annotations

import collections.abc as cabc
import os
import re
import tempfile
import typing as typ
from contextlib import suppress
from pathlib import Path

from tomlkit import parse as parse_toml
from tomlkit import string
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Item, Table

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

type _TableLike = Table | OutOfOrderTableProxy
_TABLE_LIKE_TYPES: typ.Final[tuple[type[Table], type[OutOfOrderTableProxy]]] = (
    Table,
    OutOfOrderTableProxy,
)

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


def value_as_string(value: object) -> str | None:
    """Return ``value`` as a string if possible.

    Parameters
    ----------
    value : object
        A tomlkit :class:`~tomlkit.items.Item` or a raw Python value.

    Returns
    -------
    str | None
        The string form of ``value``, or ``None`` when it is not a string.

    Examples
    --------
    >>> value_as_string("1.2.3")
    '1.2.3'
    >>> value_as_string(42) is None
    True
    """
    raw_value = value.value if isinstance(value, Item) else value
    if isinstance(raw_value, str):
        return raw_value
    return None


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
) -> Item | None:
    """Return an updated requirement value when ``value`` stores a string.

    Parameters
    ----------
    value : object
        The current requirement value, either a tomlkit
        :class:`~tomlkit.items.Item` or a raw Python value.
    target_version : str
        The target version to apply.

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
    replacement_text = compose_requirement(current, target_version)
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
    if isinstance(entry, InlineTable | Table):
        return assign_dependency_version_field(entry, target_version)
    replacement = prepare_version_replacement(entry, target_version)
    if replacement is None:
        return False
    container[key] = replacement  # type: ignore[index]  # OutOfOrderTableProxy supports item assignment
    return True


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
    >>> document = parse_toml('[dependencies]\nalpha = "0.1.0"\n')
    >>> update_section(document, ("dependencies",), {"alpha"}, "0.2.0")
    True
    """
    table = select_table(document, path)
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


def parse_manifest(manifest_path: Path) -> TOMLDocument:
    """Load ``manifest_path`` into a :class:`tomlkit` document.

    Parameters
    ----------
    manifest_path : Path
        Filesystem path to the TOML manifest to read.

    Returns
    -------
    TOMLDocument
        The parsed manifest document.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> path = Path(tempfile.mkdtemp()) / "Cargo.toml"
    >>> _ = path.write_text('version = "0.1.0"', encoding="utf-8")
    >>> parse_manifest(path)["version"]
    '0.1.0'
    """
    content = manifest_path.read_text(encoding="utf-8")
    return parse_toml(content)


def select_table(
    document: TOMLDocument | _TableLike,
    keys: tuple[str, ...],
) -> _TableLike | None:
    r"""Return the nested table located by ``keys`` if it exists.

    Parameters
    ----------
    document : TOMLDocument | _TableLike
        The document or table to descend from.
    keys : tuple[str, ...]
        Ordered keys identifying the nested table path. An empty tuple returns
        ``document`` itself when it is table-like.

    Returns
    -------
    _TableLike | None
        The nested table, or ``None`` when the path does not resolve to one.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml('[workspace.dependencies]\nalpha = "1.0.0"\n')
    >>> select_table(document, ("workspace", "dependencies"))["alpha"]
    '1.0.0'
    >>> select_table(document, ("missing",)) is None
    True
    """
    if not keys:
        return document if isinstance(document, _TABLE_LIKE_TYPES) else None
    current: object = document
    for key in keys:
        getter = getattr(current, "get", None)
        if getter is None:
            return None
        next_value = getter(key)
        if not isinstance(next_value, _TABLE_LIKE_TYPES):
            return None
        current = next_value
    return current if isinstance(current, _TABLE_LIKE_TYPES) else None


def assign_version(table: _TableLike | None, target_version: str) -> bool:
    r"""Update ``table['version']`` when ``table`` is present.

    Parameters
    ----------
    table : _TableLike | None
        The table whose ``version`` key is updated in place, or ``None``.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        ``True`` when the version field was updated.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml('[package]\nversion = "1.0.0"\n')
    >>> assign_version(document["package"], "2.0.0")
    True
    >>> document["package"]["version"]
    '2.0.0'
    >>> assign_version(None, "2.0.0")
    False
    """
    if table is None:
        return False
    current = table.get("version")
    if value_matches(current, target_version):
        return False
    if isinstance(current, Item):
        replacement = string(target_version)
        with suppress(AttributeError):  # Preserve existing formatting and comments
            replacement._trivia = current._trivia
        table["version"] = replacement
    else:
        table["version"] = target_version
    return True


def value_matches(value: object, expected: str) -> bool:
    """Return ``True`` when ``value`` already equals ``expected``.

    Parameters
    ----------
    value : object
        The current value, either a tomlkit :class:`~tomlkit.items.Item` or a
        raw Python value.
    expected : str
        The string the value is compared against.

    Returns
    -------
    bool
        ``True`` when ``value`` already equals ``expected``.

    Examples
    --------
    >>> value_matches("1.0.0", "1.0.0")
    True
    >>> value_matches("1.0.0", "2.0.0")
    False
    """
    if isinstance(value, Item):
        return value.value == expected
    return value == expected


def write_atomic_text(file_path: Path, content: str) -> None:
    """Persist ``content`` to ``file_path`` atomically using UTF-8 encoding."""
    dirpath = file_path.parent
    existing_mode: int | None = None
    with suppress(FileNotFoundError):
        existing_mode = file_path.stat().st_mode
    fd, tmp_path = tempfile.mkstemp(
        dir=dirpath,
        prefix=f"{file_path.name}.",
        text=True,
    )
    try:
        if existing_mode is not None:
            with suppress(AttributeError):
                os.fchmod(fd, existing_mode)  # not available on Windows
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        Path(tmp_path).replace(file_path)
    finally:
        with suppress(FileNotFoundError):
            Path(tmp_path).unlink()
