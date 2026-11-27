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
    confine_relative_path,
    establish_workspace_root,
    validate_relative_pattern,
)
from lading.workspace.graph_build import build_workspace_graph
from lading.workspace.implicit_members import (
    read_manifest_text,
    require_workspace_pointer,
    validate_implicit_member_manifests,
)
from lading.workspace.member_declarations import expand_member_patterns
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
    declared = expand_member_patterns(root, members, excludes)
    validate_implicit_member_manifests(root, declared, _literal_exclusions(excludes))
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
    require_workspace_pointer(root, (), document)
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


def _literal_exclusions(excludes: cabc.Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Return the literal exclusion prefixes Cargo applies."""
    return tuple(
        validate_relative_pattern(pattern, key=f"{_EXCLUDES_KEY}[{index}]")
        for index, pattern in enumerate(excludes)
    )


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
    if not _same_directory(reported_root, root):
        message = (
            f"Cargo reported workspace root {reported_root} but lading was asked "
            f"to bump {root}; pass --workspace-root {reported_root} (or run from "
            "that directory) so both agree"
        )
        raise BumpPreflightError(message)


def _same_directory(first: Path, second: Path) -> bool:
    """Return whether two existing roots identify the same directory.

    Path equality is case-insensitive on Windows, so a case-only sibling could
    pass the comparison while naming a different directory. Directory identity
    accepts alternate spellings only for the same directory.

    Returns
    -------
    bool
        ``True`` when both paths identify the same directory.
    """
    try:
        return first.samefile(second)
    except OSError:
        return False


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
