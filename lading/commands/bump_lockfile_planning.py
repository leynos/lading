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
import tomllib
import typing as typ
from pathlib import Path, PurePosixPath

from lading.commands.bump_lockfile_manifests import merge_discovered_manifests
from lading.commands.bump_lockfile_paths import LockfileRegenerationError
from lading.utils.confinement import (
    BumpPreflightError,
    ConfinedPath,
    ConfineOptions,
    TargetKind,
    confine_relative_path,
    relative_to_root,
)
from lading.workspace.metadata import CargoMetadataError, load_cargo_metadata

if typ.TYPE_CHECKING:
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
    """

    root: Path
    manifests: tuple[Path, ...]
    lockfiles: tuple[Path, ...]


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
    for config_path in _cargo_config_paths(workspace_root, environ):
        configured = _configured_lockfile_path(config_path)
        if configured:
            message = (
                f"{config_path} sets resolver.lockfile-path to {configured!r}; "
                "remove it so Cargo writes the lockfiles the bump plan validated"
            )
            raise BumpPreflightError(message)


def _cargo_config_paths(
    workspace_root: Path,
    environ: cabc.Mapping[str, str],
) -> tuple[Path, ...]:
    """Return the Cargo configuration files that could layer over the root."""
    paths: list[Path] = []
    for directory in (workspace_root, *workspace_root.parents):
        paths.extend(_config_files(directory / ".cargo"))
    cargo_home = environ.get("CARGO_HOME")
    if cargo_home:
        home = Path(cargo_home).expanduser()
        if not home.is_absolute():
            # Cargo runs with cwd=workspace_root, so a relative CARGO_HOME is
            # relative to the workspace, not to this process's directory.
            home = workspace_root / home
    else:
        home = Path.home() / ".cargo"
    paths.extend(_config_files(home))
    return tuple(dict.fromkeys(paths))


def _config_files(directory: Path) -> list[Path]:
    """Return the Cargo configuration files that exist in ``directory``."""
    return [
        candidate
        for name in ("config.toml", "config")
        if (candidate := directory / name).is_file()
    ]


def _configured_lockfile_path(config_path: Path) -> str | None:
    """Return ``resolver.lockfile-path`` from ``config_path`` when set."""
    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        message = f"cannot read Cargo configuration {config_path}: {exc}"
        raise BumpPreflightError(message) from exc
    resolver = document.get("resolver")
    if not isinstance(resolver, cabc.Mapping):
        return None
    configured = resolver.get("lockfile-path")
    return configured if isinstance(configured, str) and configured else None


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
        If a manifest is unsafe or missing, Cargo reports an unexpected
        workspace root, or a lockfile-path override is set.
    BumpTargetExaminationError
        If a configured manifest or Cargo query cannot be examined.
    LockfileRegenerationError
        If a merged manifest is not named ``Cargo.toml``.
    """  # ruff: ignore[docstring-extraneous-exception]  # delegated to the helpers
    reject_lockfile_override(
        os.environ if environ is None else environ,
        workspace_root,
    )
    merged = merge_discovered_manifests(
        workspace_root, lockfile_manifests, runner=runner
    )
    validated = _validated_manifests(workspace_root, merged)
    return _resolve_destinations(workspace_root, validated, runner=runner)


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
            raise LockfileRegenerationError(message)
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
) -> LockfilePlan:
    """Map each manifest to its effective workspace root and lockfile."""
    destinations: dict[tuple[str, ...], tuple[Path, Path]] = {}
    for manifest in manifests:
        effective_root = _effective_workspace_root(root, manifest, runner=runner)
        lockfile = _confine_lockfile(root, effective_root)
        destinations.setdefault(
            lockfile.parts,
            (effective_root / _ROOT_MANIFEST, lockfile.path),
        )
    workspace_manifests = tuple(entry[0] for entry in destinations.values())
    lockfiles = tuple(entry[1] for entry in destinations.values())
    return LockfilePlan(root=root, manifests=workspace_manifests, lockfiles=lockfiles)


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
    try:
        metadata = load_cargo_metadata(
            manifest.path.parent,
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


def _confine_lockfile(root: Path, workspace_root: Path) -> ConfinedPath:
    """Return the confined ``Cargo.lock`` destination for ``workspace_root``."""
    relative_root = relative_to_root(
        root, workspace_root, key="lockfile destination", label="workspace root"
    )
    pattern = (
        _ROOT_LOCKFILE
        if not relative_root.parts
        else PurePosixPath(relative_root.as_posix(), _ROOT_LOCKFILE).as_posix()
    )
    return confine_relative_path(
        root,
        pattern,
        key="lockfile destination",
        options=ConfineOptions(may_create=True),
    )
