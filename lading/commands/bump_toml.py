"""TOML manipulation utilities for version bumping."""

from __future__ import annotations

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
else:  # pragma: no cover - provide runtime placeholders for type checking imports
    TOMLDocument = typ.Any

type _TableLike = Table | OutOfOrderTableProxy
_TABLE_LIKE_TYPES: typ.Final[tuple[type[Table], type[OutOfOrderTableProxy]]] = (
    Table,
    OutOfOrderTableProxy,
)

NON_DIGIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^([^\d]*)")


def value_as_string(value: object) -> str | None:
    """Return ``value`` as a string if possible."""
    raw_value = value.value if isinstance(value, Item) else value
    if isinstance(raw_value, str):
        return raw_value
    return None


def compose_requirement(existing: str, target_version: str) -> str:
    """Prefix ``target_version`` with any non-numeric operator from ``existing``."""
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
    """Return an updated requirement value when ``value`` stores a string."""
    current = value_as_string(value)
    if current is None:
        return None
    replacement_text = compose_requirement(current, target_version)
    if replacement_text == current:
        return None
    replacement = string(replacement_text)
    if isinstance(value, Item):
        with suppress(AttributeError):  # Preserve inline comments and whitespace trivia
            replacement._trivia = value._trivia  # type: ignore[attr-defined]
    return replacement


def assign_dependency_version_field(
    container: InlineTable | Table,
    target_version: str,
) -> bool:
    """Update the ``version`` key of ``container`` if present."""
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
    """Update a dependency entry with ``target_version`` if it records a version."""
    if isinstance(entry, InlineTable | Table):
        return assign_dependency_version_field(entry, target_version)
    replacement = prepare_version_replacement(entry, target_version)
    if replacement is None:
        return False
    container[key] = replacement  # type: ignore[index]  # OutOfOrderTableProxy supports item assignment
    return True


def update_dependency_table(
    table: _TableLike,
    dependency_names: typ.Collection[str],
    target_version: str,
) -> bool:
    """Update dependency requirements within ``table`` for ``dependency_names``."""
    changed = False
    for name in dependency_names:
        if name not in table:
            continue
        entry = table[name]  # type: ignore[index]  # OutOfOrderTableProxy supports indexing
        if update_dependency_entry(table, name, entry, target_version):
            changed = True
    return changed


def update_section(
    document: TOMLDocument,
    path: tuple[str, ...],
    names: typ.Collection[str],
    target_version: str,
) -> bool:
    """Update dependency versions in the table at ``path``.

    Args:
        document: The parsed TOML manifest document.
        path: Tuple of keys identifying the table path (e.g., ``("workspace",
            "dependencies")``).
        names: Collection of dependency names to update.
        target_version: The target version to apply.

    Returns:
        True if any version entries were changed.

    """
    table = select_table(document, path)
    if table is None:
        return False
    return update_dependency_table(table, names, target_version)


def update_dependency_sections(
    document: TOMLDocument,
    dependency_sections: typ.Mapping[str, typ.Collection[str]],
    target_version: str,
    *,
    include_workspace_sections: bool = False,
) -> bool:
    """Apply ``target_version`` to dependency entries for the provided sections.

    Args:
        document: The parsed TOML manifest document.
        dependency_sections: Mapping of section names to crate names to update.
        target_version: The target version to apply.
        include_workspace_sections: When True, also update entries in
            ``[workspace.<section>]`` tables (e.g., ``[workspace.dependencies]``).

    Returns:
        True if any version entries were changed.

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
    """Load ``manifest_path`` into a :class:`tomlkit` document."""
    content = manifest_path.read_text(encoding="utf-8")
    return parse_toml(content)


def select_table(
    document: TOMLDocument | _TableLike,
    keys: tuple[str, ...],
) -> _TableLike | None:
    """Return the nested table located by ``keys`` if it exists."""
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
    """Update ``table['version']`` when ``table`` is present."""
    if table is None:
        return False
    current = table.get("version")
    if value_matches(current, target_version):
        return False
    if isinstance(current, Item):
        replacement = string(target_version)
        with suppress(AttributeError):  # Preserve existing formatting and comments
            replacement._trivia = current._trivia  # type: ignore[attr-defined]
        table["version"] = replacement
    else:
        table["version"] = target_version
    return True


def value_matches(value: object, expected: str) -> bool:
    """Return ``True`` when ``value`` already equals ``expected``."""
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
