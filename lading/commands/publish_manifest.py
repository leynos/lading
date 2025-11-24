"""Manifest manipulation helpers for publish staging."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

from lading import config as config_module

try:  # pragma: no cover - typing helper
    from tomlkit.toml_document import TOMLDocument
except ImportError:  # pragma: no cover - mypy fallback for runtime
    TOMLDocument = typ.Any  # type: ignore[assignment]

if typ.TYPE_CHECKING:
    from pathlib import Path

    from lading.commands.publish_plan import PublishPlan

StripPatchesSetting = config_module.StripPatchesSetting

type _ManifestValidation = (
    tuple[
        TOMLDocument,
        tuple[cabc.MutableMapping[str, typ.Any], cabc.MutableMapping[str, typ.Any]],
    ]
    | None
)


class PublishPreparationError(RuntimeError):
    """Raised when publish preparation cannot stage required assets."""


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
    crates_io: cabc.MutableMapping[str, typ.Any],
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
) -> tuple[cabc.MutableMapping[str, typ.Any], cabc.MutableMapping[str, typ.Any]] | None:
    """Return the patch mapping and crates-io table when available."""
    patch_table = document.get("patch")
    if not isinstance(patch_table, cabc.MutableMapping):
        return None
    crates_io = patch_table.get("crates-io")
    if not isinstance(crates_io, cabc.MutableMapping):
        return None
    return patch_table, crates_io


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
    patch_table: cabc.MutableMapping[str, typ.Any],
    crates_io: cabc.MutableMapping[str, typ.Any],
) -> None:
    """Remove empty patch tables from the document."""
    if not crates_io:
        patch_table.pop("crates-io", None)
    if not patch_table:
        document.pop("patch", None)


def _apply_strategy_to_patches(
    strategy: StripPatchesSetting,
    patch_table: cabc.MutableMapping[str, typ.Any],
    crates_io: cabc.MutableMapping[str, typ.Any],
    publishable_names: tuple[str, ...],
) -> bool:
    """Apply the strip patch strategy and return True if modified."""
    if strategy == "all":
        return patch_table.pop("crates-io", None) is not None
    if strategy == "per-crate":
        return _remove_per_crate_entries(crates_io, publishable_names)
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
