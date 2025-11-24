"""Manifest-related behavioural fixtures for CLI scenarios."""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers
from tomlkit import inline_table, table
from tomlkit import parse as parse_toml

if typ.TYPE_CHECKING:
    from pathlib import Path


def _update_manifest_version(
    manifest_path: Path,
    version: str,
    keys: tuple[str, ...],
) -> None:
    """Update version at nested ``keys`` path in the manifest at ``manifest_path``."""
    if not manifest_path.exists():
        message = f"Manifest not found: {manifest_path}"
        raise AssertionError(message)
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    target = document
    for key in keys[:-1]:
        try:
            target = target[key]
        except KeyError as exc:  # pragma: no cover - defensive guard
            path = "/".join(keys)
            message = f"Key path {path!r} missing from manifest {manifest_path}"
            raise AssertionError(message) from exc
    target[keys[-1]] = version
    manifest_path.write_text(document.as_string(), encoding="utf-8")


def _update_crate_manifests(crates_root: Path, version: str) -> None:
    """Update version in all crate manifests under ``crates_root``."""
    if not crates_root.exists():
        message = f"Crates directory not found: {crates_root}"
        raise AssertionError(message)
    for child in crates_root.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "Cargo.toml"
        _update_manifest_version(
            manifest_path,
            version,
            ("package", "version"),
        )


@given(parsers.parse('the workspace manifests record version "{version}"'))
def given_workspace_versions_match(
    workspace_directory: Path,
    version: str,
) -> None:
    """Ensure the workspace and member manifests record ``version``."""
    workspace_manifest = workspace_directory / "Cargo.toml"
    _update_manifest_version(
        workspace_manifest,
        version,
        ("workspace", "package", "version"),
    )
    crates_root = workspace_directory / "crates"
    _update_crate_manifests(crates_root, version)


@given(parsers.parse('the workspace file "{relative_path}" contains "{contents}"'))
def given_workspace_file_contents(
    workspace_directory: Path, relative_path: str, contents: str
) -> None:
    """Create or overwrite ``relative_path`` with ``contents`` inside the workspace."""
    target = workspace_directory / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")


@given(parsers.parse('the workspace manifest patches crates "{crate_names}"'))
def given_workspace_manifest_patch_entries(
    workspace_directory: Path,
    crate_names: str,
) -> None:
    """Ensure ``[patch.crates-io]`` defines entries for ``crate_names``."""
    manifest_path = workspace_directory / "Cargo.toml"
    if not manifest_path.exists():
        message = f"Workspace manifest not found: {manifest_path}"
        raise AssertionError(message)
    names = [name.strip() for name in crate_names.split(",") if name.strip()]
    document = parse_toml(manifest_path.read_text(encoding="utf-8"))
    patch_table = document.get("patch")
    if patch_table is None:
        patch_table = table()
        document["patch"] = patch_table
    crates_io = patch_table.get("crates-io")
    if crates_io is None:
        crates_io = table()
        patch_table["crates-io"] = crates_io
    for name in names:
        entry = inline_table()
        entry.update({"path": f"../{name}"})
        crates_io[name] = entry
    manifest_path.write_text(document.as_string(), encoding="utf-8")
