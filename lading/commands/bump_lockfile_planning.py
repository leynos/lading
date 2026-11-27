"""Plan every lockfile destination before bump rewrites any manifest.

The configured ``bump.lockfile_manifests`` and the manifests implied by tracked
``Cargo.lock`` files are validated and resolved through Cargo once, during
preparation. Each manifest is mapped to the workspace root Cargo reports for
it, so a nested manifest that belongs to the outer workspace points at the
outer lockfile instead of an adjacent file that Cargo would never write.

The resulting :class:`LockfilePlan` is immutable. Regeneration consumes it
without rediscovering manifests, which keeps the write phase inside the paths
validated by the preflight.
"""

import collections.abc as cabc
import dataclasses as dc
import os
import typing as typ
from pathlib import Path, PurePosixPath

from lading.commands.bump_lockfile_config import configured_lockfile_override
from lading.commands.bump_lockfile_manifests import merge_discovered_manifests
from lading.utils.confinement import (
    BumpPreflightError,
    ConfinedPath,
    ConfineOptions,
    TargetKind,
    confine_relative_path,
    relative_to_root,
)
from lading.workspace.metadata import CargoMetadataError, load_cargo_metadata

if typ.TYPE_CHECKING:  # pragma: no cover - import-time typing aids only
    from lading.runtime import CommandRunner

__all__ = ["LockfilePlan", "plan_lockfiles", "reject_lockfile_override"]

_ROOT_MANIFEST = "Cargo.toml"
_ROOT_LOCKFILE = "Cargo.lock"
_LOCKFILE_OVERRIDE_ENV_VAR = "CARGO_RESOLVER_LOCKFILE_PATH"


@dc.dataclass(frozen=True, slots=True)
class LockfilePlan:
    """Validated lockfile regeneration targets.

    Attributes
    ----------
    root : Path
        Canonical workspace root, used as the working directory for Cargo.
    manifests : tuple[Path, ...]
        Effective workspace-root manifests to pass to ``cargo update``.
    lockfiles : tuple[Path, ...]
        Corresponding ``Cargo.lock`` destinations, in the same order.
    environ : cabc.Mapping[str, str] | None
        Snapshot of the Cargo-relevant environment used to validate the plan,
        or ``None`` for a plan built without an environment. Regeneration
        refuses to run when the live environment differs.
    """

    root: Path
    manifests: tuple[Path, ...]
    lockfiles: tuple[Path, ...]
    environ: cabc.Mapping[str, str] | None = None


def reject_lockfile_override(
    environ: cabc.Mapping[str, str],
    workspace_root: Path,
) -> None:
    """Reject a Cargo lockfile-path override before planning.

    Cargo accepts the override from ``CARGO_RESOLVER_LOCKFILE_PATH`` or from
    ``resolver.lockfile-path`` in any configuration file it layers. Either
    route would make Cargo write a lockfile the validated plan does not name,
    so both are rejected.

    Parameters
    ----------
    environ : cabc.Mapping[str, str]
        Environment to inspect.
    workspace_root : Path
        Canonical workspace root, used to locate layered configuration files.

    Raises
    ------
    BumpPreflightError
        If the environment variable or a configuration file sets a non-empty
        lockfile path.
    """
    override = environ.get(_LOCKFILE_OVERRIDE_ENV_VAR)
    if override:
        message = (
            f"{_LOCKFILE_OVERRIDE_ENV_VAR} is set to {override!r}; unset it so "
            "Cargo writes the lockfiles the bump plan validated"
        )
        raise BumpPreflightError(message)
    found = configured_lockfile_override(workspace_root, environ)
    if found is not None:
        config_path, configured = found
        message = (
            f"{config_path} sets resolver.lockfile-path to {configured!r}; "
            "remove it so Cargo writes the lockfiles the bump plan validated"
        )
        raise BumpPreflightError(message)


def plan_lockfiles(
    workspace_root: Path,
    lockfile_manifests: cabc.Sequence[str],
    *,
    runner: CommandRunner | None = None,
    environ: cabc.Mapping[str, str] | None = None,
) -> LockfilePlan:
    """Plan lockfile destinations for ``lockfile_manifests``.

    Parameters
    ----------
    workspace_root : Path
        Canonical workspace root.
    lockfile_manifests : cabc.Sequence[str]
        Configured manifests relative to ``workspace_root``. Manifests implied
        by git-tracked lockfiles are merged before validation.
    runner : CommandRunner | None
        Command runner for Git discovery and Cargo queries.
    environ : cabc.Mapping[str, str] | None
        Environment to check for a lockfile-path override. ``None`` reads
        :data:`os.environ`; the CLI supplies it explicitly.

    Returns
    -------
    LockfilePlan
        Deduplicated effective workspace-root manifests and their lockfiles.

    Raises
    ------
    BumpPreflightError
        If a manifest is unsafe, missing, or not named ``Cargo.toml``, Cargo
        reports an unexpected workspace root, or a lockfile-path override is
        set.
    BumpTargetExaminationError
        If a configured manifest or Cargo query cannot be examined.
    """  # ruff: ignore[docstring-extraneous-exception]  # delegated to the helpers
    environment = os.environ if environ is None else environ
    reject_lockfile_override(environment, workspace_root)
    merged = merge_discovered_manifests(
        workspace_root, lockfile_manifests, runner=runner
    )
    validated = _validated_manifests(workspace_root, merged)
    return _resolve_destinations(
        workspace_root, validated, runner=runner, environ=environment
    )


def _validated_manifests(
    root: Path,
    merged: cabc.Sequence[str],
) -> tuple[ConfinedPath, ...]:
    """Return confined manifests, with the workspace root first and deduplicated."""
    candidates: list[tuple[str, str]] = [(_ROOT_MANIFEST, "workspace manifest")]
    candidates.extend(
        (manifest, f"bump.lockfile_manifests[{index}]")
        for index, manifest in enumerate(merged)
    )
    manifests: list[ConfinedPath] = []
    seen: set[tuple[str, ...]] = set()
    for pattern, key in candidates:
        confined = confine_relative_path(root, pattern, key=key)
        if confined.relative.name != _ROOT_MANIFEST:
            message = (
                f"Lockfile manifest path must point to a Cargo.toml file: {pattern}"
            )
            raise BumpPreflightError(message)
        if confined.parts in seen:
            continue
        seen.add(confined.parts)
        manifests.append(confined)
    return tuple(manifests)


def _resolve_destinations(
    root: Path,
    manifests: cabc.Sequence[ConfinedPath],
    *,
    runner: CommandRunner | None,
    environ: cabc.Mapping[str, str],
) -> LockfilePlan:
    """Map each manifest to its effective workspace root and lockfile."""
    destinations: dict[tuple[str, ...], tuple[Path, Path]] = {}
    for manifest in manifests:
        effective_root = _effective_workspace_root(root, manifest, runner=runner)
        lockfile = _confine_lockfile(root, effective_root)
        destinations.setdefault(
            lockfile.parts,
            (_confine_root_manifest(root, effective_root), lockfile.path),
        )
    workspace_manifests = tuple(entry[0] for entry in destinations.values())
    lockfiles = tuple(entry[1] for entry in destinations.values())
    return LockfilePlan(
        root=root,
        manifests=workspace_manifests,
        lockfiles=lockfiles,
        environ=dict(environ),
    )


def _effective_workspace_root(
    root: Path,
    manifest: ConfinedPath,
    *,
    runner: CommandRunner | None,
) -> Path:
    """Return the workspace root Cargo reports for ``manifest``.

    Even the workspace manifest is queried. A caller can pass a root that is
    itself a member of an outer workspace; Cargo would then write the outer
    lockfile, which is outside the plan. Requiring Cargo to confirm that the
    requested root is its own workspace root rejects that case before any
    write.

    Returns
    -------
    Path
        The confined effective workspace root for ``manifest``.

    Raises
    ------
    BumpPreflightError
        If Cargo cannot report a root, or reports a root outside the requested
        workspace.
    """
    reported_path = _reported_workspace_root(root, manifest, runner=runner)
    if reported_path == root:
        return root
    if manifest.parts == (_ROOT_MANIFEST,):
        message = (
            f"Cargo reports workspace root {reported_path} for {root}, which is "
            f"not a Cargo workspace root; pass --workspace-root {reported_path}"
        )
        raise BumpPreflightError(message)
    key = f"lockfile manifest {manifest.display} workspace root"
    relative = relative_to_root(root, reported_path, key=key, label="workspace root")
    confined = confine_relative_path(
        root,
        relative.as_posix(),
        key=key,
        options=ConfineOptions(expect=TargetKind.DIRECTORY),
    )
    return confined.path


def _reported_workspace_root(
    root: Path,
    manifest: ConfinedPath,
    *,
    runner: CommandRunner | None,
) -> Path:
    """Return the absolute workspace root Cargo reports for ``manifest``."""
    try:
        # Run from the workspace root, matching regeneration, so Cargo layers
        # the same configuration in both phases. ``--manifest-path`` selects
        # the manifest's workspace regardless of the working directory.
        metadata = load_cargo_metadata(
            root,
            runner=runner,
            no_deps=True,
            manifest_path=manifest.path,
        )
    except CargoMetadataError as exc:
        message = (
            "cannot determine the workspace root for lockfile manifest "
            f"{manifest.display}: {exc}"
        )
        raise BumpPreflightError(message) from exc
    reported = metadata.get("workspace_root")
    if not isinstance(reported, str | os.PathLike):
        message = (
            f"cargo metadata did not report a workspace_root for {manifest.display}"
        )
        raise BumpPreflightError(message)
    reported_path = Path(reported).expanduser()
    if not reported_path.is_absolute():
        message = (
            "cargo metadata reported a relative workspace_root for lockfile "
            f"manifest {manifest.display}: {reported!r}"
        )
        raise BumpPreflightError(message)
    return reported_path


def _confine_root_manifest(root: Path, workspace_root: Path) -> Path:
    """Return the confined ``Cargo.toml`` of an effective workspace root."""
    key = "lockfile root manifest"
    pattern = _root_relative_pattern(root, workspace_root, key=key, name=_ROOT_MANIFEST)
    return confine_relative_path(root, pattern, key=key).path


def _root_relative_pattern(
    root: Path,
    workspace_root: Path,
    *,
    key: str,
    name: str,
) -> str:
    """Return ``name`` relative to ``root`` inside ``workspace_root``."""
    relative_root = relative_to_root(
        root, workspace_root, key=key, label="workspace root"
    )
    if not relative_root.parts:
        return name
    return PurePosixPath(relative_root.as_posix(), name).as_posix()


def _confine_lockfile(root: Path, workspace_root: Path) -> ConfinedPath:
    """Return the confined ``Cargo.lock`` destination for ``workspace_root``."""
    key = "lockfile destination"
    pattern = _root_relative_pattern(root, workspace_root, key=key, name=_ROOT_LOCKFILE)
    return confine_relative_path(
        root,
        pattern,
        key=key,
        options=ConfineOptions(may_create=True),
    )
