"""Output formatting for the ``lading bump`` command.

This module is responsible solely for producing human-readable CLI output
from the result of a version-bump run. It is consumed exclusively by
``lading.commands.bump`` and is not part of the public API.

Classes
-------
BumpChanges
    Immutable record of files altered by a bump run. Holds three sequences:
    updated Cargo manifests, updated documentation files, and regenerated
    Cargo lockfiles.

Functions
---------
_format_result_message
    Primary entry point. Assembles the final multi-line result string from
    a ``BumpChanges`` instance, the target version string, a dry-run flag,
    and the workspace root used to relativise displayed paths.

Notes
-----
Internal helpers ``_build_changes_description``, ``_format_no_changes_message``,
``_format_header``, and ``_format_manifest_path`` are not part of the public API.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

_SINGLE_CHANGE_CATEGORY_COUNT = 1
_PAIRED_CHANGE_CATEGORY_COUNT = 2


@dc.dataclass(frozen=True, slots=True)
class BumpChanges:
    """Collection of files altered by a bump run."""

    manifests: cabc.Sequence[Path] = ()
    documents: cabc.Sequence[Path] = ()
    lockfiles: cabc.Sequence[Path] = ()


def _build_changes_description(changes: BumpChanges) -> str:
    """Build a human-readable description of changed files."""
    parts: list[str] = []
    if changes.manifests:
        parts.append(f"{len(changes.manifests)} manifest(s)")
    if changes.documents:
        parts.append(f"{len(changes.documents)} documentation file(s)")
    if changes.lockfiles:
        parts.append(f"{len(changes.lockfiles)} lockfile(s)")
    if len(parts) == _SINGLE_CHANGE_CATEGORY_COUNT:
        return parts[0]
    if len(parts) == _PAIRED_CHANGE_CATEGORY_COUNT:
        return " and ".join(parts)
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _format_no_changes_message(target_version: str, *, dry_run: bool) -> str:
    """Format message when no changes are required."""
    if dry_run:
        return (
            "Dry run; no manifest changes required; "
            f"all versions already {target_version}."
        )
    return f"No manifest changes required; all versions already {target_version}."


def _format_header(description: str, target_version: str, *, dry_run: bool) -> str:
    """Format the summary header line."""
    if dry_run:
        return f"Dry run; would update version to {target_version} in {description}:"
    return f"Updated version to {target_version} in {description}:"


def _format_result_message(
    changes: BumpChanges,
    target_version: str,
    *,
    dry_run: bool,
    workspace_root: Path,
) -> str:
    """Summarise the bump outcome for CLI presentation."""
    if not any((changes.manifests, changes.documents, changes.lockfiles)):
        return _format_no_changes_message(target_version, dry_run=dry_run)

    description = _build_changes_description(changes)
    header = _format_header(description, target_version, dry_run=dry_run)
    formatted_paths = [
        f"- {_format_manifest_path(manifest_path, workspace_root)}"
        for manifest_path in changes.manifests
    ]
    formatted_paths.extend(
        f"- {_format_manifest_path(document_path, workspace_root)} (documentation)"
        for document_path in changes.documents
    )
    formatted_paths.extend(
        f"- {_format_manifest_path(lockfile_path, workspace_root)} (lockfile)"
        for lockfile_path in changes.lockfiles
    )
    return "\n".join([header, *formatted_paths])


def _format_manifest_path(manifest_path: Path, workspace_root: Path) -> str:
    """Return ``manifest_path`` relative to ``workspace_root`` when possible."""
    try:
        relative = manifest_path.relative_to(workspace_root)
    except ValueError:
        return str(manifest_path)
    return str(relative)
