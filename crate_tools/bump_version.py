#!/usr/bin/env -S uv run
# /// script
# dependencies = ["tomlkit==0.13.*", "markdown-it-py>=3,<4"]
# ///
"""Synchronize workspace and crate versions.

This tool updates the top-level workspace version and each member crate's
version to the supplied value, keeping documentation snippets in sync. It
exists to reduce the risk of publishing mismatched versions across the
workspace.

Examples
--------
Run with the desired semantic version:
    ./scripts/bump_version.py 1.2.3

"""

from __future__ import annotations

import logging
import re
import sys
import tempfile
import typing as typ
from collections import abc as cabc
from pathlib import Path

import tomlkit
from markdown_it import MarkdownIt
from tomlkit import items as toml_items
from tomlkit.exceptions import TOMLKitError

logger = logging.getLogger(__name__)

TomlMapping = cabc.Mapping[str, typ.Any]
TomlMutableMapping = cabc.MutableMapping[str, typ.Any]

EXPECTED_ARG_COUNT = 2  # argv includes script path and the requested version

if typ.TYPE_CHECKING:  # pragma: no cover - import for typing only
    from markdown_it.token import Token


def _is_matching_fence_token(tok: Token, lang: str) -> bool:
    """Return ``True`` if ``tok`` is a fence of ``lang``.

    Examples
    --------
    >>> from markdown_it import MarkdownIt
    >>> md = '''```toml
    ... ```'''
    >>> tok = MarkdownIt("commonmark").parse(md)[0]
    >>> _is_matching_fence_token(tok, 'toml')
    True

    """
    if tok.type != "fence":
        return False
    info_lang = ((tok.info or "").split()[0] or "").lower()
    return info_lang == lang.lower()


def _extract_fence_indent(opening_line: str, fence_marker: str) -> str:
    """Return indentation preceding ``fence_marker`` in ``opening_line``.

    Examples
    --------
    >>> _extract_fence_indent('  ```toml', '```')
    '  '

    """
    pos = opening_line.find(fence_marker)
    return "" if pos < 0 else opening_line[:pos]


def _process_fence_token(
    tok: Token,
    lines: list[str],
    lang: str,
    replace_fn: cabc.Callable[[str], str],
) -> str:
    r"""Return rewritten fence text for ``tok``.

    Examples
    --------
    >>> md = '''```toml
    ... foo
    ... ```
    ... '''
    >>> tokens = MarkdownIt('commonmark').parse(md)
    >>> result = _process_fence_token(
    ...     tokens[0], md.splitlines(keepends=True), 'toml', str.upper
    ... )
    >>> result == '```toml\\nFOO\\n```\\n'
    True

    """
    if tok.map is None:
        message = "Fence token is missing positional mapping data"
        raise ValueError(message)
    start, _ = tok.map
    fence_marker = tok.markup or "```"
    indent = _extract_fence_indent(lines[start], fence_marker)
    info = tok.info or lang
    original_body = tok.content
    new_body = replace_fn(original_body)
    m = re.search(r"(\r?\n+)$", original_body)
    suffix = m.group(1) if m else ""
    new_body = new_body.rstrip("\r\n") + suffix
    indented = "".join(f"{indent}{line}" for line in new_body.splitlines(keepends=True))
    return f"{indent}{fence_marker}{info}\n{indented}{indent}{fence_marker}\n"


def replace_fences(
    md_text: str, lang: str, replace_fn: cabc.Callable[[str], str]
) -> str:
    r"""Apply ``replace_fn`` to fenced code blocks of ``lang`` in Markdown text.

    Parameters
    ----------
    md_text
        Markdown content to process.
    lang
        Fence language (for example ``"toml"``).
    replace_fn
        Callback invoked with the fence body; its return value replaces the
        original body.

    Examples
    --------
    >>> md = '''```toml
    ... [dependencies]
    ... foo = "1"
    ... ```'''
    >>> replaced = replace_fences(md, 'toml', lambda body: body.replace('1', '2'))
    >>> replaced == '```toml\\n[dependencies]\\nfoo = "2"\\n```\\n'
    True

    """
    md = MarkdownIt("commonmark")
    tokens = md.parse(md_text)
    lines = md_text.splitlines(keepends=True)
    out: list[str] = []
    last = 0
    for tok in tokens:
        if not _is_matching_fence_token(tok, lang) or tok.map is None:
            continue
        start, end = tok.map
        out.append("".join(lines[last:start]))
        out.append(_process_fence_token(tok, lines, lang, replace_fn))
        last = end
    out.append("".join(lines[last:]))
    return "".join(out)


def _update_package_version(
    doc: TomlMutableMapping,
    version: str,
) -> None:
    """Update package version in ``doc`` if present.

    Examples
    --------
    >>> data = {"package": {"version": "0"}}
    >>> _update_package_version(data, "1")
    >>> data["package"]["version"]
    '1'

    """
    match doc:
        case {"workspace": {"package": cabc.MutableMapping() as package}}:
            package["version"] = version
        case {"package": cabc.MutableMapping() as package}:
            package["version"] = version


def _extract_version_prefix(
    entry: toml_items.String | TomlMapping | str | None,
) -> str:
    """Return version prefix (``^`` or ``~``) if present.

    Examples
    --------
    >>> import tomlkit
    >>> doc = tomlkit.parse('foo = "^1"')
    >>> _extract_version_prefix(doc["foo"])
    '^'
    >>> _extract_version_prefix("1")
    ''

    """
    if isinstance(entry, cabc.Mapping):
        mapping = typ.cast("TomlMapping", entry)
        entry = mapping.get("version")
    text = entry.value if isinstance(entry, toml_items.String) else str(entry or "")
    return text[0] if text and text[0] in "^~" else ""


def _infer_string_type(item: toml_items.String) -> toml_items.StringType:
    """Return the tomlkit string type that matches ``item``'s quoting style."""
    raw = item.as_string()
    if raw.startswith('"' * 3):
        return toml_items.StringType.MLB
    if raw.startswith("'''"):
        return toml_items.StringType.MLL
    if raw.startswith("'"):
        return toml_items.StringType.SLL
    return toml_items.StringType.SLB


def _clone_string_with_value(item: toml_items.String, value: str) -> toml_items.String:
    """Produce a new tomlkit string preserving ``item``'s trivia and quoting."""
    # ``String.from_raw`` and trivia mutation are stable in tomlkit 0.13+, so we
    # rebuild the node instead of mutating private attributes.

    replacement = toml_items.String.from_raw(value, type_=_infer_string_type(item))
    replacement.trivia.indent = item.trivia.indent
    replacement.trivia.comment = item.trivia.comment
    replacement.trivia.comment_ws = item.trivia.comment_ws
    replacement.trivia.trail = item.trivia.trail
    return replacement


def _update_dict_dependency(
    entry: TomlMutableMapping,
    version: str,
) -> None:
    """Update dict-style dependency ``entry`` with ``version``.

    Examples
    --------
    >>> import tomlkit
    >>> entry = tomlkit.table()
    >>> entry["version"] = tomlkit.string("^0.1")
    >>> _update_dict_dependency(entry, "1.2.3")
    >>> tomlkit.dumps(entry).strip()
    'version = "^1.2.3"'

    """
    if bool(entry.get("workspace")) is True:
        return
    prefix = _extract_version_prefix(entry)
    existing = entry.get("version")
    if isinstance(existing, toml_items.String):
        entry["version"] = _clone_string_with_value(existing, prefix + version)
    else:
        entry["version"] = prefix + version


def _update_string_dependency(
    deps: TomlMutableMapping,
    dependency: str,
    entry: toml_items.String | str,
    version: str,
) -> None:
    """Update string-style dependency ``dependency`` in ``deps``.

    Examples
    --------
    >>> import tomlkit
    >>> doc = tomlkit.parse('foo = "^0.1"')
    >>> _update_string_dependency(doc, 'foo', doc['foo'], '1.2.3')
    >>> tomlkit.dumps(doc).strip()
    'foo = "^1.2.3"'

    """
    prefix = _extract_version_prefix(entry)
    if isinstance(entry, toml_items.String):
        deps[dependency] = _clone_string_with_value(entry, prefix + version)
    else:
        deps[dependency] = prefix + version


def _update_dependency_in_table(
    deps: TomlMutableMapping,
    dependency: str,
    version: str,
) -> None:
    """Update ``dependency`` inside dependency table ``deps``.

    Examples
    --------
    >>> import tomlkit
    >>> doc = tomlkit.parse('''[dependencies]
    ... foo = "^0.1"''')
    >>> _update_dependency_in_table(doc['dependencies'], 'foo', '1.2.3')
    >>> 'foo = "^1.2.3"' in tomlkit.dumps(doc)
    True

    """
    entry = deps[dependency]
    if isinstance(entry, cabc.Mapping):
        _update_dict_dependency(entry, version)
    else:
        _update_string_dependency(deps, dependency, entry, version)


def _update_dependency_version(
    doc: TomlMutableMapping,
    dependency: str,
    version: str,
) -> None:
    """Update ``dependency`` across dependency tables in ``doc``.

    Maintains caret or tilde prefixes and preserves formatting.

    Examples
    --------
    >>> doc = tomlkit.parse('''[dependencies]
    ... foo = "^0.1"''')
    >>> _update_dependency_version(doc, 'foo', '1.2.3')
    >>> tomlkit.dumps(doc).strip() == '''[dependencies]
    ... foo = "^1.2.3"'''
    True

    >>> snippet = '''[dependencies]
    ... foo = { version = "~0.1", features = ["a"] }'''
    >>> doc = tomlkit.parse(snippet)
    >>> _update_dependency_version(doc, 'foo', '1.2.3')
    >>> 'version = "~1.2.3"' in tomlkit.dumps(doc)
    True

    >>> doc = tomlkit.parse('''[dev-dependencies]
    ... foo = "^0.1"''')
    >>> _update_dependency_version(doc, 'foo', '1.2.3')
    >>> 'foo = "^1.2.3"' in tomlkit.dumps(doc)
    True

    """
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = doc.get(table)
        if not deps or dependency not in deps:
            continue
        _update_dependency_in_table(deps, dependency, version)


def _set_version(
    toml_path: Path,
    version: str,
    dependency: str | None = None,
    doc: TomlMutableMapping | None = None,
) -> None:
    """Set package and optional dependency version in a ``Cargo.toml``.

    Parameters
    ----------
    toml_path
        Path to the ``Cargo.toml`` file.
    version
        Version string to apply.
    dependency
        Optional dependency to update alongside the package version.
    doc
        Pre-parsed document to update. If provided, the file is not re-read.

    """
    if doc is None:
        with toml_path.open("r", encoding="utf-8") as fh:
            doc = tomlkit.parse(fh.read())

    _update_package_version(doc, version)

    if dependency:
        _update_dependency_version(doc, dependency, version)

    text = tomlkit.dumps(doc)
    temp_dir = toml_path.parent
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=temp_dir, delete=False
    ) as tf:
        tf.write(text)
        temp_name = tf.name
    Path(temp_name).replace(toml_path)


def _validate_args_and_setup(argv: list[str]) -> tuple[str, Path] | None:
    """Validate CLI arguments and resolve the workspace root.

    Parameters
    ----------
    argv
        Raw command-line arguments.

    Returns
    -------
    tuple[str, Path] | None
        The requested version and repository root if arguments are valid;
        ``None`` otherwise.

    Examples
    --------
    >>> version, root = _validate_args_and_setup(["bump_version.py", "1.2.3"])
    >>> (version, isinstance(root, Path))
    ('1.2.3', True)

    """
    if len(argv) != EXPECTED_ARG_COUNT:
        prog = Path(argv[0]).name
        print(f"Usage: {prog} <version>", file=sys.stderr)
        return None
    version = argv[1]
    root = Path(__file__).resolve().parent.parent
    return version, root


def _resolve_member_paths(root: Path, members: list[str]) -> list[Path]:
    """Expand workspace member patterns to concrete paths.

    Parameters
    ----------
    root
        Workspace root directory.
    members
        Glob patterns for workspace members.

    Returns
    -------
    list[Path]
        Paths matched by the supplied patterns. Warnings are emitted for
        patterns that match nothing.

    Examples
    --------
    >>> [path.as_posix() for path in _resolve_member_paths(Path('.'), ['scripts'])]
    ['scripts']

    """
    paths: list[Path] = []
    for pattern in members:
        matches = list(root.glob(pattern))
        if not matches:
            print(
                f"Warning: No members matched pattern '{pattern}'",
                file=sys.stderr,
            )
            continue
        paths.extend(matches)
    return paths


def _update_member_version(member_path: Path, version: str) -> bool:
    r"""Update a member Cargo.toml with the supplied version.

    Parameters
    ----------
    member_path
        Path to the member's ``Cargo.toml`` file.
    version
        Semantic version to apply.

    Returns
    -------
    bool
        ``True`` if an error occurred while updating; ``False`` otherwise.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     cargo_toml = Path(tmp) / "Cargo.toml"
    ...     _ = cargo_toml.write_text(
    ...         "[package]\\n"
    ...         'name = "demo"\\n'
    ...         'version = "0.1.0"\\n'
    ...     )
    ...     result = _update_member_version(cargo_toml, "1.2.3")
    ...     cargo_toml.read_text(), result
    ('[package]\\nname = "demo"\\nversion = "1.2.3"\\n', False)

    """
    try:
        # Derive from the actual package name to avoid coupling to directory names
        with member_path.open("r", encoding="utf-8") as fh:
            doc = tomlkit.parse(fh.read())
        package_name = doc.get("package", {}).get("name")
        dep = "ortho_config_macros" if package_name == "ortho_config" else None
        _set_version(member_path, version, dep, doc)
    except (
        TOMLKitError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:  # pragma: no cover - defensive
        print(
            f"Error: Failed to set version for {member_path}: {exc}",
            file=sys.stderr,
        )
        return True
    return False


def _process_single_member(member_root: Path, version: str) -> bool:
    """Process a single workspace member.

    Parameters
    ----------
    member_root
        Directory or file matched from the workspace ``members`` patterns.
    version
        Semantic version to apply.

    Returns
    -------
    bool
        ``True`` if updating the member failed; ``False`` otherwise.

    Examples
    --------
    >>> _process_single_member(Path('scripts'), '1.2.3')
    False

    """
    member_path = member_root / "Cargo.toml" if member_root.is_dir() else member_root
    if not member_path.exists():
        print(
            f"Warning: Skipping missing member Cargo.toml at {member_path}",
            file=sys.stderr,
        )
        return False
    return _update_member_version(member_path, version)


def _process_members(root: Path, members: list[str], version: str) -> bool:
    """Update all workspace members to the supplied version.

    Parameters
    ----------
    root
        Workspace root directory.
    members
        Glob patterns for workspace members.
    version
        Semantic version to apply.

    Returns
    -------
    bool
        ``True`` if any member failed to update; ``False`` otherwise.

    Examples
    --------
    >>> _process_members(Path('.'), ['scripts'], '1.2.3')
    False

    """
    had_error = False
    for member_root in _resolve_member_paths(root, members):
        if _process_single_member(member_root, version):
            had_error = True
    return had_error


def replace_version_in_toml(snippet: str, version: str) -> str:
    r"""Update ``ortho_config`` version in a TOML snippet.

    Examples
    --------
    >>> replace_version_in_toml('''[dependencies]
    ... ortho_config = "0"
    ... ''', '1') == '[dependencies]\\northo_config = "1"\\n'
    True
    >>> replace_version_in_toml('''[dependencies]
    ... ortho_config = "0"''', '1') == '[dependencies]\\northo_config = "1"'
    True

    """
    try:
        doc = tomlkit.parse(snippet)
    except TOMLKitError:
        return snippet
    match = re.search(r"((?:\r?\n)*)$", snippet)
    newline_suffix = match.group(1) if match else ""
    dependency_found = False
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = doc.get(table)
        if deps and "ortho_config" in deps:
            dependency_found = True
            _update_dependency_in_table(deps, "ortho_config", version)
    if not dependency_found:
        return snippet
    dumped = tomlkit.dumps(doc)
    base = dumped.rstrip("\r\n")
    return f"{base}{newline_suffix}" if newline_suffix else base


_replace_version_in_toml = replace_version_in_toml


def _update_markdown_versions(md_path: Path, version: str) -> None:
    """Update ``ortho_config`` versions in TOML fences within ``md_path``.

    Examples
    --------
    >>> import tempfile
    >>> sample = '''```toml
    ... [dependencies]
    ... ortho_config = "0"
    ... ```
    ... '''
    >>> with tempfile.TemporaryDirectory() as tmpdir:
    ...     md_path = Path(tmpdir) / 'sample.md'
    ...     _ = md_path.write_text(sample, encoding='utf-8')
    ...     _update_markdown_versions(md_path, '1')
    ...     'ortho_config = "1"' in md_path.read_text(encoding='utf-8')
    True

    """
    if not md_path.exists():
        return
    text = md_path.read_text(encoding="utf-8")
    updated = replace_fences(
        text, "toml", lambda body: replace_version_in_toml(body, version)
    )
    md_path.write_text(updated, encoding="utf-8")


def _warn_on_markdown_update_failure(md_path: Path, version: str) -> None:
    """Emit a structured log if updating Markdown snippets raises an expected error."""
    try:
        _update_markdown_versions(md_path, version)
    except (TOMLKitError, OSError):
        logger.exception(
            "Failed to update Markdown fence versions in %s to %s",
            md_path,
            version,
            exc_info=True,
        )


def main(argv: list[str]) -> int:
    """Update the workspace and member crate versions to the supplied value.

    Parameters
    ----------
    argv
        Command-line arguments where `argv[1]` is the target semantic version
        (for example, "1.2.3").

    Returns
    -------
    int
        Zero on success; non-zero if any member update fails or arguments are
        invalid.

    Examples
    --------
    >>> main(["bump_version.py", "1.2.3"])  # doctest: +SKIP
    0

    """
    result = _validate_args_and_setup(argv)
    if result is None:
        return 1
    version, root = result
    workspace = root / "Cargo.toml"
    try:
        with workspace.open("r", encoding="utf-8") as fh:
            data = tomlkit.parse(fh.read())
    except (
        TOMLKitError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:  # pragma: no cover - defensive
        print(f"Error: Failed to parse {workspace}: {exc}", file=sys.stderr)
        return 1
    members = data.get("workspace", {}).get("members", [])
    try:
        _set_version(workspace, version, doc=data)
    except (TOMLKitError, OSError, TypeError, ValueError) as exc:
        print(
            f"Error: Failed to set version for {workspace}: {exc}",
            file=sys.stderr,
        )
        return 1
    had_error = _process_members(root, members, version)
    for md_path in (root / "README.md", root / "docs" / "users-guide.md"):
        _warn_on_markdown_update_failure(md_path, version)
    return 0 if not had_error else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
