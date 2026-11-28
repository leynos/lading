"""Output formatting for the ``lading bump`` command.

This module turns bump command results into human-readable CLI messages. Callers
provide a :class:`BumpChanges` record, the target version, dry-run state, and
workspace root, then receive the exact multi-line string that should be printed
to the terminal. The module is consumed by ``lading.commands.bump`` and is not a
general-purpose formatting API.

Classes
-------
BumpChanges
    Public value object describing updated manifests, documentation files,
    transposed readmes, and lockfiles.

Functions
---------
_format_result_message
    Primary entry point. Assembles the final multi-line result string from
    a ``BumpChanges`` instance, the target version string, a dry-run flag,
    and the workspace root used to relativise displayed paths.

Notes
-----
Internal helpers ``_build_changes_description``, ``_format_no_changes_message``,
``_format_header``, ``_format_manifest_path``, and ``_has_changes`` are not part
of the public API.

This module is the sole owner of ``BumpChanges`` and bump result-message
formatting (issue #95); no other module may re-declare them.
"""

import collections.abc as cabc
import dataclasses as dc

# ``Path`` is imported at runtime (not under ``TYPE_CHECKING``) so CrossHair can
# resolve the annotations on the pure helpers when model-checking their
# contracts (issue #95). ``ruff`` TC003 is intentionally ignored for this file's
# stdlib type-only imports.
from pathlib import Path

from lading.commands.bump_preview import DocumentationPreview as DocumentationPreview

_SINGLE_CHANGE_CATEGORY_COUNT = 1
_PAIRED_CHANGE_CATEGORY_COUNT = 2


@dc.dataclass(frozen=True, slots=True)
class BumpChanges:
    """Collection of files altered by a bump run.

    The dataclass is frozen and slot-based, so callers receive an immutable,
    compact record that can be passed safely between bump processing and output
    formatting.

    Attributes
    ----------
    manifests : Sequence[Path]
        Cargo manifests updated by the bump run.
    documents : Sequence[Path]
        Documentation files updated by TOML snippet rewriting.
    lockfiles : Sequence[Path]
        Cargo lockfiles regenerated after manifest updates.
    transposed_readmes : Sequence[Path]
        Crate README files adopted from the workspace README.
    document_previews : Sequence[DocumentationPreview]
        Exact original and proposed text for each rewritten documentation
        file, in preparation order. Empty when no documentation file changed.
    """

    manifests: cabc.Sequence[Path] = ()
    documents: cabc.Sequence[Path] = ()
    lockfiles: cabc.Sequence[Path] = ()
    transposed_readmes: cabc.Sequence[Path] = ()
    document_previews: cabc.Sequence[DocumentationPreview] = ()


@dc.dataclass(frozen=True, slots=True)
class _ChangeCounts:
    """Number of changed files in each bump output category."""

    manifests: int = 0
    documents: int = 0
    readmes: int = 0
    lockfiles: int = 0


def _change_counts(changes: BumpChanges) -> _ChangeCounts:
    """Return the per-category counts that drive the summary text."""
    return _ChangeCounts(
        manifests=len(changes.manifests),
        documents=len(changes.documents),
        readmes=len(changes.transposed_readmes),
        lockfiles=len(changes.lockfiles),
    )


def _has_change_counts(counts: _ChangeCounts) -> bool:
    """Return True when at least one category has a non-zero count."""
    return any((
        counts.manifests,
        counts.documents,
        counts.readmes,
        counts.lockfiles,
    ))


def _build_changes_description(counts: _ChangeCounts) -> str:
    """Build a human-readable description of changed files.

    CrossHair contracts (issue #95); model-check with ``make crosshair``. The
    parameter is a count record rather than ``BumpChanges`` because the pinned
    CrossHair cannot build proxies for the ``Path`` values nested inside
    ``DocumentationPreview``; counts let it explore every non-empty category.

    pre: min(counts.manifests, counts.documents, counts.readmes, counts.lockfiles) >= 0
    pre: _has_change_counts(counts)
    post: len(__return__) > 0
    post: " and and " not in __return__
    """
    parts: list[str] = []
    if counts.manifests:
        parts.append(f"{counts.manifests} manifest(s)")
    if counts.documents:
        parts.append(f"{counts.documents} documentation file(s)")
    if counts.readmes:
        parts.append(f"{counts.readmes} readme file(s)")
    if counts.lockfiles:
        parts.append(f"{counts.lockfiles} lockfile(s)")
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
    """Format the summary header line.

    CrossHair contracts (issue #95); model-check with ``make crosshair``.

    post: __return__.endswith(":")
    post: target_version in __return__
    post: description in __return__
    """
    if dry_run:
        return f"Dry run; would update version to {target_version} in {description}:"
    return f"Updated version to {target_version} in {description}:"


def _has_changes(changes: BumpChanges) -> bool:
    """Return True when a bump run changed at least one file category."""
    return _has_change_counts(_change_counts(changes))


def _format_result_message(
    changes: BumpChanges,
    target_version: str,
    *,
    dry_run: bool,
    workspace_root: Path,
) -> str:
    """Summarise the bump outcome for CLI presentation."""
    if not _has_changes(changes):
        return _format_no_changes_message(target_version, dry_run=dry_run)

    description = _build_changes_description(_change_counts(changes))
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
        f"- {_format_manifest_path(readme_path, workspace_root)} (readme)"
        for readme_path in changes.transposed_readmes
    )
    formatted_paths.extend(
        f"- {_format_manifest_path(lockfile_path, workspace_root)} (lockfile)"
        for lockfile_path in changes.lockfiles
    )
    return "\n".join([header, *formatted_paths])


def _format_manifest_path(manifest_path: Path, workspace_root: Path) -> str:
    """Return ``manifest_path`` relative to ``workspace_root`` when possible.

    CrossHair contracts (issue #95); model-check with ``make crosshair``.

    post: isinstance(__return__, str)

    Falls back to returning ``manifest_path`` exactly as given, unmodified,
    when it cannot be made relative to ``workspace_root``.
    """
    try:
        relative = manifest_path.relative_to(workspace_root)
    except ValueError:
        return str(manifest_path)
    return str(relative)
