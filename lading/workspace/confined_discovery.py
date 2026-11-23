"""Confined, read-only workspace discovery for ``lading bump``.

Bump used to discover the workspace through ``cargo metadata`` alone. That
accepted a symlinked member directory or a symlinked manifest, because the
resolved path was whatever Cargo happened to report. This module validates the
original ``[workspace].members`` and ``[workspace].exclude`` patterns lexically,
expands them without following links, and only then asks Cargo for the member
set. Every reported member manifest is confined and checked to be an existing
regular ``Cargo.toml`` before :func:`build_workspace_graph` reads any manifest.

The metadata call uses ``--no-deps``: bump needs the member set, and dependency
resolution is what makes Cargo want to write ``Cargo.lock``. The read-only
contract is exercised by the real-Cargo integration lane.

Publish keeps its existing discovery path; this module is bump-only.
"""

import collections.abc as cabc
import os
import typing as typ
from pathlib import Path, PurePosixPath

from tomlkit import parse
from tomlkit.exceptions import TOMLKitError

from lading import toml_coerce
from lading.utils.confinement import (
    BumpPreflightError,
    ConfinedPath,
    GlobOptions,
    MatchExpectation,
    MissingBumpTargetError,
    UnmatchedPolicy,
    confine_relative_path,
    establish_workspace_root,
    expand_confined_glob,
    relative_to_root,
)
from lading.workspace.graph_build import build_workspace_graph
from lading.workspace.implicit_members import (
    read_manifest_text,
    validate_implicit_member_manifests,
)
from lading.workspace.metadata import load_cargo_metadata
from lading.workspace.models import WorkspaceGraph, WorkspaceModelError

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.runtime import CommandRunner

__all__ = ["load_bump_workspace"]

_ROOT_MANIFEST = "Cargo.toml"
_MEMBERS_KEY = "workspace.members"
_EXCLUDES_KEY = "workspace.exclude"


def load_bump_workspace(
    workspace_root: Path | str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> WorkspaceGraph:
    """Return a workspace graph discovered through confined, read-only checks.

    Parameters
    ----------
    workspace_root : Path | str | None
        Workspace root. ``None`` selects the current working directory.
    runner : CommandRunner | None
        Explicit command runner for the ``cargo metadata`` invocation. ``None``
        uses the ambient or default runner.

    Returns
    -------
    WorkspaceGraph
        The workspace graph. Its ``workspace_root`` is canonical, and every
        crate manifest path is a confined existing regular file.

    Raises
    ------
    BumpPreflightError
        If a member or exclusion pattern is unsafe, a declared member is
        missing or lacks a regular ``Cargo.toml``, Cargo reports a different
        workspace root, or a reported member manifest escapes the workspace or
        is not a regular file.
    BumpTargetExaminationError
        If a manifest or directory cannot be examined or read.
    MissingBumpTargetError
        If the workspace manifest or a declared member does not exist.
    WorkspaceModelError
        If Cargo's metadata cannot be turned into a workspace graph.

    Examples
    --------
    >>> graph = load_bump_workspace()  # doctest: +SKIP
    >>> graph.workspace_root  # doctest: +SKIP
    PosixPath('/workspace')
    """  # ruff: ignore[docstring-extraneous-exception]  # propagated from the helpers
    root = establish_workspace_root(workspace_root)
    members, excludes = _read_workspace_tables(root)
    declared = _expand_member_patterns(root, members, excludes)
    validate_implicit_member_manifests(root, declared)
    metadata = load_cargo_metadata(root, runner=runner, no_deps=True)
    _require_cargo_workspace_root(root, metadata)
    reported = _validate_reported_member_manifests(root, metadata)
    _cross_check_declared_members(declared, reported)
    return build_workspace_graph(metadata)


def _read_workspace_tables(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the declared member and exclusion patterns from the root manifest."""
    manifest = confine_relative_path(root, _ROOT_MANIFEST, key="workspace manifest")
    text = read_manifest_text(manifest.path, key="workspace manifest")
    try:
        document = parse(text)
    except TOMLKitError as exc:
        message = f"workspace manifest is not valid TOML: {manifest.display}: {exc}"
        raise BumpPreflightError(message) from exc
    workspace = document.get("workspace")
    if not isinstance(workspace, cabc.Mapping):
        # A package-only manifest is a valid single-package workspace; Cargo
        # reports the root package itself as the sole member.
        return (), ()
    workspace_mapping = typ.cast("cabc.Mapping[str, typ.Any]", workspace)
    return (
        _string_sequence(workspace_mapping.get("members"), _MEMBERS_KEY),
        _string_sequence(workspace_mapping.get("exclude"), _EXCLUDES_KEY),
    )


def _string_sequence(value: object, key: str) -> tuple[str, ...]:
    """Coerce a TOML array of strings, naming ``key`` in any diagnostic."""
    if value is None:
        return ()
    sequence = toml_coerce.expect_sequence(value, key, error=BumpPreflightError)
    return toml_coerce.validate_string_sequence(sequence, key, error=BumpPreflightError)


def _expand_member_patterns(
    root: Path,
    members: cabc.Sequence[str],
    excludes: cabc.Sequence[str],
) -> dict[tuple[str, ...], str]:
    """Expand member patterns, apply exclusions, and return declared members.

    Returns
    -------
    dict[tuple[str, ...], str]
        Member directory components mapped to the pattern that declared them.

    Raises
    ------
    BumpPreflightError
        If a pattern is unsafe, a literal member is missing, a glob matches
        nothing, a matched directory is not a real directory, or a matched
        member has no regular ``Cargo.toml``.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by expand_confined_glob
    excluded = _expand_exclusions(root, excludes)
    declared: dict[tuple[str, ...], str] = {}
    for index, pattern in enumerate(members):
        key = f"{_MEMBERS_KEY}[{index}]"
        is_glob = bool(_GLOB_METACHARACTERS.intersection(pattern))
        matches = _member_matches(root, pattern, key=key)
        for match in matches:
            if is_glob and _is_excluded(match.parts, excluded):
                continue
            _require_member_manifest(root, match, key=key, pattern=pattern)
            declared[match.parts] = pattern
    return declared


_GLOB_METACHARACTERS: typ.Final[frozenset[str]] = frozenset("*?[")


def _member_matches(
    root: Path,
    pattern: str,
    *,
    key: str,
) -> tuple[ConfinedPath, ...]:
    """Expand one member pattern, accepting an explicit ``"."`` for the root."""
    if _is_root_member(pattern):
        return (ConfinedPath(root=root, parts=()),)
    return expand_confined_glob(
        root,
        pattern,
        key=key,
        options=GlobOptions(
            expect=MatchExpectation.DIRECTORY,
            unmatched=UnmatchedPolicy.ERROR,
        ),
    )


def _is_root_member(pattern: str) -> bool:
    """Return whether ``pattern`` explicitly names the workspace root."""
    stripped = pattern.strip()
    return stripped in {".", "./", ".\\"}


def _is_excluded(
    member_parts: tuple[str, ...],
    excluded: cabc.Set[tuple[str, ...]],
) -> bool:
    """Return whether ``member_parts`` is ``excluded`` or lies beneath it.

    Cargo applies an exclusion to a matching directory and its descendants;
    an exact comparison would leave ``crates/retired/app`` a member when
    ``crates/retired`` is excluded. Explicitly listed members keep Cargo's
    precedence, so only glob-derived matches are filtered.

    Returns
    -------
    bool
        ``True`` when the member is excluded or lies beneath an exclusion.
    """
    return any(
        member_parts[: len(excluded_parts)] == excluded_parts
        for excluded_parts in excluded
    )


def _expand_exclusions(
    root: Path,
    excludes: cabc.Sequence[str],
) -> set[tuple[str, ...]]:
    """Return the directory components excluded from the member set."""
    excluded: set[tuple[str, ...]] = set()
    for index, pattern in enumerate(excludes):
        matches = expand_confined_glob(
            root,
            pattern,
            key=f"{_EXCLUDES_KEY}[{index}]",
            options=GlobOptions(
                expect=MatchExpectation.DIRECTORY,
                unmatched=UnmatchedPolicy.IGNORE,
            ),
        )
        excluded.update(match.parts for match in matches)
    return excluded


def _require_member_manifest(
    root: Path,
    match: ConfinedPath,
    *,
    key: str,
    pattern: str,
) -> None:
    """Require ``match`` to contain a confined regular ``Cargo.toml``."""
    manifest_pattern = PurePosixPath(*match.parts, _ROOT_MANIFEST).as_posix()
    try:
        confine_relative_path(root, manifest_pattern, key=key)
    except MissingBumpTargetError as exc:
        message = (
            f"{key}: workspace member {match.display!r} matched by "
            f"{pattern!r} has no Cargo.toml"
        )
        raise MissingBumpTargetError(message) from exc


def _require_cargo_workspace_root(
    root: Path,
    metadata: cabc.Mapping[str, typ.Any],
) -> None:
    """Require Cargo to agree on the canonical workspace root."""
    reported = metadata.get("workspace_root")
    if not isinstance(reported, str | os.PathLike):
        message = "cargo metadata did not report a workspace_root path"
        raise BumpPreflightError(message)
    reported_root = Path(reported).expanduser().resolve(strict=False)
    if reported_root != root:
        message = (
            f"Cargo reported workspace root {reported_root} but lading was asked "
            f"to bump {root}; pass --workspace-root {reported_root} (or run from "
            "that directory) so both agree"
        )
        raise BumpPreflightError(message)


def _validate_reported_member_manifests(
    root: Path,
    metadata: cabc.Mapping[str, typ.Any],
) -> set[tuple[str, ...]]:
    """Validate every Cargo-reported member manifest.

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


def _cross_check_declared_members(
    declared: cabc.Mapping[tuple[str, ...], str],
    reported: cabc.Set[tuple[str, ...]],
) -> None:
    """Require every declared member to appear in Cargo's reported member set."""
    missing = sorted(set(declared) - set(reported))
    if not missing:
        return
    details = ", ".join(PurePosixPath(*parts).as_posix() for parts in missing)
    message = (
        "workspace.members declared member(s) that cargo metadata did not "
        f"report: {details}"
    )
    raise BumpPreflightError(message)
