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
    BumpTargetExaminationError,
    ConfinedPath,
    GlobOptions,
    MatchExpectation,
    MissingBumpTargetError,
    UnmatchedPolicy,
    confine_relative_path,
    establish_workspace_root,
    expand_confined_glob,
    validate_relative_pattern,
)
from lading.workspace.graph_build import build_workspace_graph
from lading.workspace.implicit_members import (
    read_manifest_text,
    validate_implicit_member_manifests,
)
from lading.workspace.metadata import load_cargo_metadata
from lading.workspace.metadata_validation import validate_reported_member_manifests

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.runtime import CommandRunner
    from lading.workspace.models import WorkspaceGraph

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
    reported = validate_reported_member_manifests(root, metadata)
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
    _require_workspace_pointer(root, document)
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


def _require_workspace_pointer(
    root: Path,
    document: cabc.Mapping[str, typ.Any],
) -> None:
    """Reject a ``package.workspace`` pointer that names a different root.

    Cargo follows the pointer and reads the target workspace's manifests, so
    the requested root must already be that workspace. Rejecting the pointer
    before the metadata call keeps Cargo from opening an unvalidated file.

    Raises
    ------
    BumpPreflightError
        If the pointer names a root other than ``root``.
    """
    package = document.get("package")
    if not isinstance(package, cabc.Mapping):
        return
    pointer = package.get("workspace")
    if not isinstance(pointer, str) or not pointer.strip():
        return
    target = Path(pointer)
    if not target.is_absolute():
        target = root / target
    normalised = Path(os.path.normpath(target))
    if normalised != root:
        message = (
            f"workspace manifest points at {normalised}, not {root}; pass "
            f"--workspace-root {normalised} so the requested root is the "
            "workspace Cargo reports"
        )
        raise BumpPreflightError(message)


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
                _examine_excluded_member(root, match, key=key)
                continue
            _require_member_manifest(root, match, key=key, pattern=pattern)
            declared[match.parts] = pattern
    return declared


_GLOB_METACHARACTERS: typ.Final[frozenset[str]] = frozenset("*?[")


def _examine_excluded_member(
    root: Path,
    match: ConfinedPath,
    *,
    key: str,
) -> None:
    """Validate an excluded match's manifest when one already exists.

    Cargo still parses an excluded directory's manifest when an explicit member
    covers it, so a symlink or special file there must be rejected before the
    metadata call. An excluded directory without a manifest is not an error.

    Raises
    ------
    BumpTargetExaminationError
        If the manifest cannot be examined.
    UnsafeBumpTargetError
        If the manifest is a symlink or is not a regular file.
    """  # ruff: ignore[docstring-extraneous-exception]  # raised by confinement
    manifest = match.path / _ROOT_MANIFEST
    try:
        os.lstat(manifest)
    except FileNotFoundError:
        return
    except OSError as exc:
        message = f"{key}: cannot examine {match.display}: {exc}"
        raise BumpTargetExaminationError(message) from exc
    pattern = PurePosixPath(*match.parts, _ROOT_MANIFEST).as_posix()
    confine_relative_path(root, pattern, key=key)


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
    """Return whether ``pattern`` explicitly names the workspace root.

    Whitespace is significant: ``" . "`` names a directory whose name contains
    spaces, while ``"."`` and ``"./"`` name the root itself. ``Path`` collapses
    dot components but preserves everything else.

    Returns
    -------
    bool
        ``True`` when the pattern leaves no component below the root.
    """
    if not pattern.strip():
        return False
    return Path(pattern).parts == ()


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
    """Return the literal directory components excluded from the member set.

    Cargo treats ``exclude`` entries as literal path prefixes and does not
    expand wildcards there, so a wildcard character names a directory with that
    character in its name. No filesystem access is needed: an exclusion may
    name a directory that does not exist.

    Returns
    -------
    set[tuple[str, ...]]
        Excluded directory component tuples.
    """
    del root
    return {
        validate_relative_pattern(pattern, key=f"{_EXCLUDES_KEY}[{index}]")
        for index, pattern in enumerate(excludes)
    }


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
