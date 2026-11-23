"""Build a real bump workspace plus the Cargo metadata payload a stub returns.

Command-level preflight tests drive ``lading.cli`` in process, so the workspace
must exist on disk and the metadata stub must agree with it. This helper keeps
the two in step and exposes the crate manifests for assertions.
"""

import collections.abc as cabc
import dataclasses as dc
import typing as typ
from pathlib import Path

from tomlkit import array, document, dumps, table


@dc.dataclass(frozen=True, slots=True)
class CliWorkspace:
    """A materialised workspace and its Cargo metadata payload.

    Attributes
    ----------
    root : Path
        Canonical workspace root.
    metadata : dict[str, typ.Any]
        Payload a ``cargo metadata`` stub returns.
    crate_manifests : dict[str, Path]
        Member manifests keyed by crate name.
    """

    root: Path
    metadata: dict[str, typ.Any]
    crate_manifests: dict[str, Path]


def _write_root_manifest(
    root: Path,
    members: cabc.Sequence[str],
    *,
    root_package: bool,
) -> None:
    """Write the root manifest with optional package and workspace tables."""
    package = '[package]\nname = "root"\nversion = "0.1.0"\n\n' if root_package else ""
    members_literal = ", ".join(f'"{member}"' for member in members)
    (root / "Cargo.toml").write_text(
        package
        + f"[workspace]\nmembers = [{members_literal}]\n\n"
        + '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def _write_member(
    root: Path,
    name: str,
    *,
    readme_workspace: bool,
    version: str = "0.1.0",
) -> Path:
    """Write one member crate and return its manifest path."""
    crate_root = root / "crates" / name
    (crate_root / "src").mkdir(parents=True)
    (crate_root / "src" / "lib.rs").write_text("", encoding="utf-8")
    readme_line = "readme.workspace = true\n" if readme_workspace else ""
    manifest = crate_root / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "{version}"\n{readme_line}',
        encoding="utf-8",
    )
    return manifest


def _package_entry(name: str, manifest: Path, *, crate_id: str) -> dict[str, typ.Any]:
    """Return one Cargo metadata package entry."""
    return {
        "id": crate_id,
        "name": name,
        "version": "0.1.0",
        "manifest_path": str(manifest),
        "dependencies": [],
        "publish": None,
    }


@dc.dataclass(frozen=True, slots=True)
class WorkspaceOptions:
    """Optional shape of the workspace to create.

    Attributes
    ----------
    members : cabc.Sequence[str]
        Member crate names, each created under ``crates/<name>``.
    root_package : bool
        Whether the root manifest also declares a package.
    readme_workspace : bool
        Whether every member sets ``readme.workspace = true``.
    documentation_globs : cabc.Sequence[str]
        Documentation globs written to ``lading.toml``.
    lockfile_manifests : cabc.Sequence[str]
        Lockfile manifests written to ``lading.toml``.
    workspace_readme : str | None
        Workspace README text, or ``None`` to omit the README.
    """

    members: cabc.Sequence[str] = ("alpha",)
    root_package: bool = False
    readme_workspace: bool = False
    documentation_globs: cabc.Sequence[str] = ()
    lockfile_manifests: cabc.Sequence[str] = ()
    workspace_readme: str | None = None


def create_cli_workspace(
    root: Path,
    *,
    options: WorkspaceOptions | None = None,
) -> CliWorkspace:
    """Materialise a workspace and its metadata payload.

    Parameters
    ----------
    root : Path
        Directory to create; it must not already contain the workspace files.
    options : WorkspaceOptions | None
        Workspace shape. ``None`` creates one ``alpha`` member.

    Returns
    -------
    CliWorkspace
        The materialised workspace, payload, and member manifests.
    """
    shape = options or WorkspaceOptions()
    members = shape.members
    root.mkdir(parents=True, exist_ok=True)
    member_patterns = [f"crates/{name}" for name in members]
    _write_root_manifest(root, member_patterns, root_package=shape.root_package)
    packages: list[dict[str, typ.Any]] = []
    member_ids: list[str] = []
    manifests: dict[str, Path] = {}
    if shape.root_package:
        packages.append(_package_entry("root", root / "Cargo.toml", crate_id="root-id"))
        member_ids.append("root-id")
    for name in members:
        manifest = _write_member(root, name, readme_workspace=shape.readme_workspace)
        manifests[name] = manifest
        packages.append(_package_entry(name, manifest, crate_id=f"{name}-id"))
        member_ids.append(f"{name}-id")
    if shape.workspace_readme is not None:
        (root / "README.md").write_text(shape.workspace_readme, encoding="utf-8")
    _write_config(
        root,
        documentation_globs=shape.documentation_globs,
        lockfile_manifests=shape.lockfile_manifests,
    )
    return CliWorkspace(
        root=root,
        metadata={
            "workspace_root": str(root),
            "packages": packages,
            "workspace_members": member_ids,
        },
        crate_manifests=manifests,
    )


def _write_config(
    root: Path,
    *,
    documentation_globs: cabc.Sequence[str],
    lockfile_manifests: cabc.Sequence[str],
) -> None:
    """Write ``lading.toml`` with the supplied bump settings."""
    if not documentation_globs and not lockfile_manifests:
        return
    config = document()
    bump = table()
    if lockfile_manifests:
        lockfiles = array()
        for manifest in lockfile_manifests:
            lockfiles.append(manifest)
        bump.add("lockfile_manifests", lockfiles)
    if documentation_globs:
        documentation = table()
        globs = array()
        for pattern in documentation_globs:
            globs.append(pattern)
        documentation.add("globs", globs)
        bump.add("documentation", documentation)
    config.add("bump", bump)
    (root / "lading.toml").write_text(dumps(config), encoding="utf-8")


__all__ = ["CliWorkspace", "WorkspaceOptions", "create_cli_workspace"]
