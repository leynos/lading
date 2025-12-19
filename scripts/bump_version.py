"""Compatibility wrapper for the legacy ``bump_version.py`` script.

The historic implementation lived in a legacy package that has since been
removed. The functionality is now provided by the :mod:`lading` CLI.
"""

from __future__ import annotations

import re
import sys
import typing as typ
from collections import abc as cabc

import tomlkit
from tomlkit import items as toml_items
from tomlkit.exceptions import TOMLKitError

from lading import cli as lading_cli
from lading.commands import bump as bump_command

ReplaceFn = cabc.Callable[[str], str]


def replace_fences(md_text: str, lang: str, replace_fn: ReplaceFn) -> str:
    """Replace Markdown code fences of ``lang`` using ``replace_fn``."""
    return bump_command._replace_markdown_fences(md_text, lang, replace_fn)


def _extract_version_prefix(entry: object) -> str:
    """Return version prefix (``^`` or ``~``) if present."""
    if isinstance(entry, cabc.Mapping):
        mapping = typ.cast("cabc.Mapping[str, object]", entry)
        entry = mapping.get("version")
    text = entry.value if isinstance(entry, toml_items.String) else str(entry or "")
    return text[0] if text and text[0] in "^~" else ""


def _infer_string_type(item: toml_items.String) -> toml_items.StringType:
    raw = item.as_string()
    if raw.startswith('"' * 3):
        return toml_items.StringType.MLB
    if raw.startswith("'''"):
        return toml_items.StringType.MLL
    if raw.startswith("'"):
        return toml_items.StringType.SLL
    return toml_items.StringType.SLB


def _clone_string_with_value(item: toml_items.String, value: str) -> toml_items.String:
    replacement = toml_items.String.from_raw(value, type_=_infer_string_type(item))
    replacement.trivia.indent = item.trivia.indent
    replacement.trivia.comment = item.trivia.comment
    replacement.trivia.comment_ws = item.trivia.comment_ws
    replacement.trivia.trail = item.trivia.trail
    return replacement


def _update_dependency_in_table(deps: object, dependency: str, version: str) -> None:
    if not hasattr(deps, "__contains__") or dependency not in deps:  # type: ignore[operator]
        return
    entry = deps[dependency]  # type: ignore[index]
    if isinstance(entry, toml_items.String):
        prefix = _extract_version_prefix(entry)
        deps[dependency] = _clone_string_with_value(entry, prefix + version)  # type: ignore[index]
        return
    if isinstance(entry, dict):
        if bool(entry.get("workspace")) is True:
            return
        prefix = _extract_version_prefix(entry)
        existing = entry.get("version")
        if isinstance(existing, toml_items.String):
            entry["version"] = _clone_string_with_value(existing, prefix + version)
        else:
            entry["version"] = prefix + version
        return
    prefix = _extract_version_prefix(entry)
    deps[dependency] = f"{prefix}{version}"  # type: ignore[index]


def replace_version_in_toml(snippet: str, version: str) -> str:
    """Update ``ortho_config`` version in a TOML snippet.

    This is a narrow compatibility shim retained for callers that relied on the
    historical helper function.
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


def main(argv: typ.Sequence[str] | None = None) -> int:
    """Compatibility entry point for the legacy ``bump_version.py`` script."""
    if argv is None:
        argv = sys.argv
    tokens = list(argv[1:])
    if not tokens:
        print("Usage: bump_version.py <version> [--workspace-root <path>] [--dry-run]")
        return 2
    return lading_cli.main(["bump", *tokens])


__all__ = ["main", "replace_fences", "replace_version_in_toml"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
