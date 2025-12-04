"""Publish manifest utilities.

Summary
-------
Helpers for reading, mutating, and persisting staged ``Cargo.toml`` files
used during ``lading publish``. These functions keep formatting and trivia
intact while applying the configured patch-stripping strategy.

Functions / Call sites
----------------------
* :func:`_apply_strip_patch_strategy` — invoked from ``publish.run`` after the
  workspace is staged, to enforce ``publish.strip_patches`` settings.
* Supporting helpers (_load_manifest_document, _resolve_patch_tables, etc.)
  encapsulate TOML parsing/writing and patch-table cleanup.

Examples
--------
>>> from pathlib import Path
>>> from lading.commands.publish_manifest import _apply_strip_patch_strategy
>>> from lading.commands.publish_plan import PublishPlan
>>> plan = PublishPlan(
...     workspace_root=Path("."),
...     publishable=(),
...     skipped_manifest=(),
...     skipped_configuration=(),
... )
>>> _apply_strip_patch_strategy(Path("build/workspace"), plan, "all")

"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError
from tomlkit.toml_document import TOMLDocument

from lading import config as config_module

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from pathlib import Path

    from lading.commands.publish_plan import PublishPlan

StripPatchesSetting = config_module.StripPatchesSetting

type _ManifestValidation = (
    tuple[
        TOMLDocument,
        tuple[cabc.MutableMapping[str, object], cabc.MutableMapping[str, object]],
    ]
    | None
)


class PublishPreparationError(RuntimeError):
    """Publish staging failed to prepare required assets.

    Raised when:
        * the staged workspace manifest cannot be read or parsed.
        * manifest writes fail while applying patch stripping.

    Examples
    --------
    >>> from lading.commands.publish_manifest import PublishPreparationError
    >>> try:
    ...     raise PublishPreparationError("Workspace manifest not found")
    ... except PublishPreparationError as exc:
    ...     print(str(exc))
    Workspace manifest not found

    """


def _load_manifest_document(manifest_path: Path) -> TOMLDocument:
    """Parse and return the staged workspace manifest."""
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        message = f"Workspace manifest not found at {manifest_path}"
        raise PublishPreparationError(message) from exc
    except (PermissionError, OSError) as exc:  # pragma: no cover - defensive guard
        message = f"Unable to read workspace manifest at {manifest_path}: {exc}"
        raise PublishPreparationError(message) from exc
    try:
        return parse_toml(text)
    except TOMLKitError as exc:
        message = f"Failed to parse staged workspace manifest: {manifest_path}"
        raise PublishPreparationError(message) from exc


def _write_manifest_document(manifest_path: Path, document: TOMLDocument) -> None:
    """Persist ``document`` back to ``manifest_path`` preserving trivia."""
    text = document.as_string()
    if not text.endswith("\n"):
        text = f"{text}\n"
    try:
        manifest_path.write_text(text, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive guard
        message = f"Failed to write manifest to {manifest_path}: {exc}"
        raise PublishPreparationError(message) from exc


def _remove_per_crate_entries(
    crates_io: cabc.MutableMapping[str, object],
    crate_names: cabc.Iterable[str],
) -> bool:
    """Remove entries for ``crate_names`` and return ``True`` when modified."""
    removed = False
    # Deduplicate crate names while preserving order for deterministic updates
    for crate in dict.fromkeys(crate_names):
        if crates_io.pop(crate, None) is not None:
            removed = True
    return removed


def _resolve_patch_tables(
    document: TOMLDocument,
) -> tuple[cabc.MutableMapping[str, object], cabc.MutableMapping[str, object]] | None:
    """Return the patch mapping and crates-io table when available."""
    match document:
        case {"patch": cabc.MutableMapping() as patch_table}:
            match patch_table:
                case {"crates-io": cabc.MutableMapping() as crates_io}:
                    return patch_table, crates_io
                case _:
                    return None
        case _:
            return None


def _validate_and_load_manifest(
    staging_root: Path, strategy: StripPatchesSetting
) -> _ManifestValidation:
    """Load and validate the manifest for patch stripping.

    Returns the document and patch tables when applicable, or None if
    stripping should be skipped.

    """
    if strategy is False:
        return None
    manifest_path = staging_root / "Cargo.toml"
    if not manifest_path.exists():
        return None
    document = _load_manifest_document(manifest_path)
    patch_tables = _resolve_patch_tables(document)
    return None if patch_tables is None else (document, patch_tables)


def _cleanup_empty_patch_tables(
    document: TOMLDocument,
    patch_table: cabc.MutableMapping[str, object],
    crates_io: cabc.MutableMapping[str, object],
) -> None:
    """Remove empty patch tables from the document."""
    if not crates_io:
        patch_table.pop("crates-io", None)
    if not patch_table:
        document.pop("patch", None)


def _apply_strategy_to_patches(
    strategy: StripPatchesSetting,
    patch_table: cabc.MutableMapping[str, object],
    crates_io: cabc.MutableMapping[str, object],
    publishable_names: tuple[str, ...],
) -> bool:
    """Apply the strip patch strategy and return True if modified."""
    match strategy:
        case "all":
            return patch_table.pop("crates-io", None) is not None
        case "per-crate":
            return _remove_per_crate_entries(crates_io, publishable_names)
        case _:
            message = f"Unsupported strip patch strategy: {strategy}"
            raise PublishPreparationError(message)


def _apply_strip_patch_strategy(
    staging_root: Path,
    plan: PublishPlan,
    strategy: StripPatchesSetting,
) -> None:
    """Modify the staged manifest according to ``publish.strip_patches``."""
    validation = _validate_and_load_manifest(staging_root, strategy)
    if validation is None:
        return
    document, patch_tables = validation
    patch_table, crates_io = patch_tables
    manifest_path = staging_root / "Cargo.toml"

    modified = _apply_strategy_to_patches(
        strategy,
        patch_table,
        crates_io,
        plan.publishable_names,
    )
    if not modified:
        return

    _cleanup_empty_patch_tables(document, patch_table, crates_io)
    _write_manifest_document(manifest_path, document)
