"""TOML table, version-field, and manifest helpers for version bumping.

Dependency-section rewriting lives in
:mod:`lading.commands.bump_toml_dependencies`.
"""

import os
import stat
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

type TableLike = Table | InlineTable | OutOfOrderTableProxy
TABLE_LIKE_TYPES: typ.Final[
    tuple[type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...]
] = (
    Table,
    InlineTable,
    OutOfOrderTableProxy,
)
BLOCK_TABLE_TYPES: typ.Final[
    tuple[type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...]
] = (Table, OutOfOrderTableProxy)


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
    content = _read_manifest_text(manifest_path)
    return parse_toml(content)


def _read_manifest_text(manifest_path: Path) -> str:
    """Read manifest text without blocking on a substituted non-regular file.

    Confinement rejects a non-regular target when it runs, but another process
    can replace the path before this read. Opening non-blocking and checking
    the opened descriptor's type keeps a FIFO from blocking the run while it
    holds the workspace lock.

    Parameters
    ----------
    manifest_path : Path
        Filesystem path to the manifest to read.

    Returns
    -------
    str
        The decoded manifest text.

    Raises
    ------
    OSError
        If the path cannot be opened, the opened descriptor is not a regular
        file, or the text cannot be read or decoded.
    """
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(manifest_path, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            message = f"Manifest path is not a regular file: {manifest_path}"
            raise OSError(message)
        return handle.read()


def select_table(
    document: TOMLDocument | TableLike,
    keys: tuple[str, ...],
    *,
    include_inline: bool = False,
) -> TableLike | None:
    r"""Return the nested table located by ``keys`` if it exists.

    Parameters
    ----------
    document : TOMLDocument | TableLike
        The document or table to descend from.
    keys : tuple[str, ...]
        Ordered keys identifying the nested table path. An empty tuple returns
        ``document`` itself when it is table-like.
    include_inline : bool, default False
        Whether an inline table counts as a table to descend through. The
        documentation and manifest passes leave inline shapes alone; a
        configured non-member rewrite opts in.

    Returns
    -------
    TableLike | None
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
    allowed: tuple[
        type[Table] | type[InlineTable] | type[OutOfOrderTableProxy], ...
    ] = TABLE_LIKE_TYPES if include_inline else BLOCK_TABLE_TYPES
    if not keys:
        return document if isinstance(document, allowed) else None
    current: object = document
    for key in keys:
        getter = getattr(current, "get", None)
        if getter is None:
            return None
        next_value = getter(key)
        if not isinstance(next_value, allowed):
            return None
        current = next_value
    return current if isinstance(current, allowed) else None


def _version_item(current: object, target_version: str) -> Item | str:
    """Return ``target_version`` reusing ``current``'s formatting and comments."""
    if not isinstance(current, Item):
        return target_version
    replacement = string(target_version)
    with suppress(AttributeError):  # Preserve existing formatting and comments
        replacement._trivia = current._trivia
    return replacement


def assign_version(table: TableLike | None, target_version: str) -> bool:
    r"""Insert or replace ``table['version']`` when ``table`` is present.

    This is the manifest helper: it writes ``target_version`` whether or not
    the key already exists. Documentation snippets use
    :func:`replace_string_version` instead, which never inserts a key.

    Parameters
    ----------
    table : TableLike | None
        The table whose ``version`` key is inserted or updated in place, or
        ``None``.
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
    table["version"] = _version_item(current, target_version)
    return True


def replace_string_version(table: TableLike | None, target_version: str) -> bool:
    r"""Replace ``table['version']`` only when it is an existing string.

    Unlike :func:`assign_version`, this helper never inserts a missing key and
    never replaces an inherited or non-string value. The documentation pass
    uses it so a TOML fence that documents metadata, an unrelated package, or
    ``version.workspace = true`` keeps its meaning.

    Parameters
    ----------
    table : TableLike | None
        The table whose existing string ``version`` is replaced in place.
    target_version : str
        The target version to apply.

    Returns
    -------
    bool
        ``True`` when a differing string version was replaced.

    Examples
    --------
    >>> from tomlkit import parse as parse_toml
    >>> document = parse_toml('[package]\nversion = "1.0.0"\n')
    >>> replace_string_version(document["package"], "2.0.0")
    True
    >>> document["package"]["version"]
    '2.0.0'
    >>> replace_string_version(document["package"], "2.0.0")
    False
    >>> missing = parse_toml('[package]\nname = "demo"\n')
    >>> replace_string_version(missing["package"], "2.0.0")
    False
    >>> "version" in missing["package"]
    False
    """
    if table is None or value_as_string(table.get("version")) is None:
        return False
    return assign_version(table, target_version)


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
