r"""Manipulate TOML values and files used by version bumping.

The module selects tables, preserves TOML formatting while replacing scalar
values, and writes changed files atomically. Dependency-table traversal lives
in :mod:`lading.commands.bump_toml_dependencies`.

Examples
--------
>>> from tomlkit import parse as parse_toml
>>> from lading.commands.bump_toml import assign_version, select_table
>>> document = parse_toml('[package]\nversion = "1.0.0"')
>>> assign_version(select_table(document, ("package",)), "2.0.0")
True
>>> document["package"]["version"]
'2.0.0'
"""

import logging
import os
import re
import tempfile
import typing as typ
from contextlib import suppress
from pathlib import Path

from tomlkit import parse as parse_toml
from tomlkit import string
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import InlineTable, Item, String, Table

from lading.commands.bump_requirement_bounds import (
    excluding_retained_bounds,
    excluding_rewritten_first_bound,
)

if typ.TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

type _TableLike = Table | InlineTable | OutOfOrderTableProxy
_TABLE_LIKE_TYPES: typ.Final[
    tuple[type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...]
] = (
    Table,
    InlineTable,
    OutOfOrderTableProxy,
)

NON_DIGIT_PREFIX: typ.Final[re.Pattern[str]] = re.compile(r"^([^\d]*)")
_log = logging.getLogger(__name__)


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
    trailing_whitespace = ""
    if separator:
        trimmed_constraint = first_constraint.rstrip()
        trailing_whitespace = first_constraint[len(trimmed_constraint) :]
    else:
        trimmed_constraint = first_constraint

    match = NON_DIGIT_PREFIX.match(trimmed_constraint)
    if not match:
        replacement = target_version
    else:
        prefix = match.group(1)
        if not prefix or prefix == trimmed_constraint:
            replacement = target_version
        else:
            replacement = f"{prefix}{target_version}"

    return f"{replacement}{trailing_whitespace}{separator}{remaining_constraints}"


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
    excluded_bounds = (
        *excluding_rewritten_first_bound(current, target_version),
        *excluding_retained_bounds(current, target_version),
    )
    if excluded_bounds:
        _log.warning(
            "Target version %s does not satisfy preserved Cargo comparator(s) %s "
            "in requirement %r; the rewritten requirement may not resolve",
            target_version,
            ", ".join(excluded_bounds),
            current,
        )
    replacement_text = compose_requirement(current, target_version)
    if replacement_text == current:
        return None
    replacement = string(replacement_text)
    if isinstance(value, Item):
        with suppress(AttributeError):  # Preserve inline comments and whitespace trivia
            replacement._trivia = value._trivia
    return replacement


def prepare_string_value_replacement(
    value: object,
    replacement_text: str,
) -> Item | None:
    """Return string content with its TOML style and trivia preserved.

    Parameters
    ----------
    value : object
        The current TOML value, either a tomlkit :class:`~tomlkit.items.Item`
        or a raw Python value.
    replacement_text : str
        The complete string value to store.

    Returns
    -------
    Item | None
        The replacement item, or ``None`` when ``value`` is not a string or
        already contains ``replacement_text``.

    Examples
    --------
    >>> from tomlkit import parse
    >>> document = parse("path = 'old' # keep")
    >>> document["path"] = prepare_string_value_replacement(
    ...     document["path"], "new"
    ... )
    >>> document.as_string()
    "path = 'new' # keep"
    >>> prepare_string_value_replacement(42, "new") is None
    True
    """
    current = value_as_string(value)
    if current is None or current == replacement_text:
        return None
    match value:
        case String() as styled_string:
            original_text = styled_string.as_string()
            literal = original_text.startswith("'")
            multiline = original_text.startswith(("'''", '"""'))
        case _:
            literal = multiline = False
    replacement = string(
        replacement_text,
        literal=literal,
        multiline=multiline,
    )
    if multiline and replacement_text.startswith("\n"):
        rendered = replacement.as_string()
        reparsed = parse_toml(f"value = {rendered}")["value"]
        if str(reparsed) != replacement_text:
            repaired = f"{rendered[:3]}\n{rendered[3:]}"
            replacement = parse_toml(f"value = {repaired}")["value"]
    match value:
        case Item() as original_item:
            with suppress(
                AttributeError
            ):  # Preserve inline comments and whitespace trivia
                replacement._trivia = original_item._trivia
    return replacement


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
        The document or table to descend from, including inline tables.
    keys : tuple[str, ...]
        Ordered keys identifying the nested table path. An empty tuple returns
        ``document`` itself when it is table-like.

    Returns
    -------
    _TableLike | None
        The nested table or inline table, or ``None`` when the path does not
        resolve to one.

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
