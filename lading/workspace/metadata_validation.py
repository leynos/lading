"""Validate the Cargo metadata payload before the workspace graph is built.

The loader asks Cargo for the member set and then, before any manifest is read,
requires every reported member to be a confined existing regular ``Cargo.toml``
beneath the canonical root. Keeping the payload schema checks here separates
them from the TOML and glob policy in
:mod:`lading.workspace.confined_discovery`.
"""

import collections.abc as cabc
import os
import typing as typ
from pathlib import Path

from lading.utils.confinement import (
    BumpPreflightError,
    confine_relative_path,
    relative_to_root,
)
from lading.workspace.models import WorkspaceModelError

__all__ = ["validate_reported_member_manifests"]

_ROOT_MANIFEST = "Cargo.toml"


def validate_reported_member_manifests(
    root: Path,
    metadata: cabc.Mapping[str, typ.Any],
) -> set[tuple[str, ...]]:
    """Validate every Cargo-reported member manifest.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    metadata : cabc.Mapping[str, typ.Any]
        Parsed ``cargo metadata`` payload.

    Returns
    -------
    set[tuple[str, ...]]
        The parent directory components of each validated manifest, so the
        result is comparable with the declared member directories.

    Raises
    ------
    BumpPreflightError
        If the metadata payload is malformed.
    WorkspaceModelError
        If a member identifier is missing from the package list.
    UnsafeBumpTargetError
        If a reported manifest escapes the workspace or is not regular.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by helpers
    packages_by_id = _packages_by_id(metadata)
    member_ids = _metadata_sequence(
        metadata.get("workspace_members"), "workspace_members"
    )
    return {
        _reported_member(root, packages_by_id, member_id) for member_id in member_ids
    }


def _packages_by_id(
    metadata: cabc.Mapping[str, typ.Any],
) -> dict[str, cabc.Mapping[str, typ.Any]]:
    """Return the metadata package entries keyed by identifier."""
    packages = _metadata_sequence(metadata.get("packages"), "packages")
    packages_by_id: dict[str, cabc.Mapping[str, typ.Any]] = {}
    for package in packages:
        package_mapping = _metadata_mapping(package, "packages[]")
        package_id = package_mapping.get("id")
        if not isinstance(package_id, str):
            message = "cargo metadata package id must be a string"
            raise BumpPreflightError(message)
        packages_by_id[package_id] = package_mapping
    return packages_by_id


def _reported_member(
    root: Path,
    packages_by_id: cabc.Mapping[str, cabc.Mapping[str, typ.Any]],
    member_id: object,
) -> tuple[str, ...]:
    """Return one reported member's validated manifest directory."""
    if not isinstance(member_id, str):
        message = "cargo metadata workspace_members entries must be strings"
        raise BumpPreflightError(message)
    package = packages_by_id.get(member_id)
    if package is None:
        message = f"workspace member {member_id!r} missing from package list"
        raise WorkspaceModelError(message)
    return _validate_member_manifest(root, package, member_id)


def _metadata_sequence(value: object, field: str) -> cabc.Sequence[object]:
    """Return ``value`` as a metadata sequence, rejecting a malformed payload."""
    if isinstance(value, str | bytes) or not isinstance(value, cabc.Sequence):
        message = f"cargo metadata {field} must be a sequence"
        raise BumpPreflightError(message)
    return value


def _metadata_mapping(value: object, field: str) -> cabc.Mapping[str, typ.Any]:
    """Return ``value`` as a metadata mapping, rejecting a malformed payload."""
    if not isinstance(value, cabc.Mapping):
        message = f"cargo metadata {field} entries must be mappings"
        raise BumpPreflightError(message)
    return typ.cast("cabc.Mapping[str, typ.Any]", value)


def _validate_member_manifest(
    root: Path,
    package: cabc.Mapping[str, typ.Any],
    member_id: str,
) -> tuple[str, ...]:
    """Return the confined directory components of one member's manifest.

    Parameters
    ----------
    root : Path
        Canonical workspace root.
    package : cabc.Mapping[str, typ.Any]
        One ``cargo metadata`` package entry.
    member_id : str
        Package identifier used in diagnostics.

    Returns
    -------
    tuple[str, ...]
        The member directory's components below the workspace root.

    Raises
    ------
    WorkspaceModelError
        If the package's ``manifest_path`` is not a path.
    BumpPreflightError
        If the manifest is not named ``Cargo.toml``.
    UnsafeBumpTargetError
        If the manifest escapes the workspace or a component is a symlink.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by the shared helpers
    manifest_value = package.get("manifest_path")
    if not isinstance(manifest_value, str | os.PathLike):
        message = f"workspace member {member_id!r} manifest_path must be a path"
        raise WorkspaceModelError(message)
    manifest_path = Path(manifest_value).expanduser()
    key = f"workspace member {member_id!r} manifest"
    relative = relative_to_root(root, manifest_path, key=key, label="manifest")
    if relative.name != _ROOT_MANIFEST:
        message = f"{key} must be named {_ROOT_MANIFEST}: {manifest_path}"
        raise BumpPreflightError(message)
    confined = confine_relative_path(root, relative.as_posix(), key=key)
    return confined.parts[:-1]
