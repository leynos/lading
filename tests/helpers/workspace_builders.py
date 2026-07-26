"""Workspace construction helpers for bump command tests."""

from __future__ import annotations

import dataclasses as dc
import textwrap
import typing as typ

from tomlkit import parse as parse_toml

from lading import config as config_module
from lading.workspace import WorkspaceCrate, WorkspaceDependency, WorkspaceGraph

if typ.TYPE_CHECKING:
    from pathlib import Path


@dc.dataclass(frozen=True, slots=True)
class _CrateSpec:
    """Specification for constructing workspace crates in tests."""

    name: str
    manifest_extra: str = ""
    dependencies: tuple[WorkspaceDependency, ...] = ()
    version: str = "0.1.0"
    readme_workspace: bool = False


def _write_workspace_manifest(root: Path, members: tuple[str, ...]) -> Path:
    """Create a minimal workspace manifest with the provided members."""
    from tomlkit import array, document, dumps, table

    manifest = root / "Cargo.toml"
    doc = document()
    workspace_table = table()
    members_array = array()
    for member in members:
        members_array.append(member)
    workspace_table.add("members", members_array)
    doc.add("workspace", workspace_table)
    package_table = table()
    package_table.add("version", "0.1.0")
    doc["workspace"]["package"] = package_table
    manifest.write_text(dumps(doc), encoding="utf-8")
    return manifest


def _write_crate_manifest(manifest_path: Path, spec: _CrateSpec) -> None:
    """Write a crate manifest based on the provided crate specification."""
    header_lines = [
        "[package]",
        f'name = "{spec.name}"',
        f'version = "{spec.version}"',
    ]
    if spec.readme_workspace:
        header_lines.append("readme.workspace = true")
    content = "\n".join(header_lines) + "\n"
    if spec.manifest_extra:
        content += "\n" + textwrap.dedent(spec.manifest_extra).strip() + "\n"
    manifest_path.write_text(content, encoding="utf-8")


def _build_workspace_with_internal_deps(
    root: Path, *, specs: tuple[_CrateSpec, ...]
) -> tuple[WorkspaceGraph, dict[str, Path]]:
    """Create a workspace populated with crates and return manifest paths."""
    root.mkdir(parents=True, exist_ok=True)
    members = tuple(f"crates/{spec.name}" for spec in specs)
    _write_workspace_manifest(root, members)

    manifests: dict[str, Path] = {}
    crates: list[WorkspaceCrate] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            message = f"Duplicate crate name in specs: {spec.name!r}"
            raise ValueError(message)
        seen.add(spec.name)
        crate_dir = root / "crates" / spec.name
        crate_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = crate_dir / "Cargo.toml"
        _write_crate_manifest(manifest_path, spec)
        manifests[spec.name] = manifest_path
        crates.append(
            WorkspaceCrate(
                id=f"{spec.name}-id",
                name=spec.name,
                version=spec.version,
                manifest_path=manifest_path,
                root_path=crate_dir,
                publish=True,
                readme_is_workspace=spec.readme_workspace,
                dependencies=spec.dependencies,
            )
        )
    workspace = WorkspaceGraph(workspace_root=root, crates=tuple(crates))
    return workspace, manifests


def _make_workspace(tmp_path: Path) -> WorkspaceGraph:
    """Construct a workspace graph with two member crates."""
    alpha_spec = _CrateSpec(name="alpha")
    beta_spec = _CrateSpec(name="beta")
    return _build_workspace_with_internal_deps(tmp_path, specs=(alpha_spec, beta_spec))[
        0
    ]


def _load_version(path: Path, table: tuple[str, ...]) -> str:
    """Return the version string stored at ``table`` within ``path``."""
    document = parse_toml(path.read_text(encoding="utf-8"))
    current = document
    try:
        for key in table:
            current = current[key]
        return current["version"]
    except KeyError as error:
        dotted = ".".join((*table, "version"))
        message = f"Missing key path '{dotted}' in {path}"
        raise KeyError(message) from error


def _make_config(
    *,
    exclude: tuple[str, ...] = (),
    documentation_globs: tuple[str, ...] | None = None,
) -> config_module.LadingConfig:
    """Construct a configuration instance for tests."""
    bump_mapping: dict[str, typ.Any] = {}
    if exclude:
        bump_mapping["exclude"] = exclude
    if documentation_globs is not None:
        bump_mapping["documentation"] = {"globs": documentation_globs}
    bump_config = config_module.BumpConfig.from_mapping(bump_mapping)
    return config_module.LadingConfig(bump=bump_config)


def _create_alpha_crate(workspace_root: Path) -> WorkspaceCrate:
    """Create the alpha crate and return its workspace representation."""
    alpha_dir = workspace_root / "crates" / "alpha"
    alpha_dir.mkdir(parents=True, exist_ok=True)
    alpha_manifest = alpha_dir / "Cargo.toml"
    alpha_manifest.write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return WorkspaceCrate(
        id="alpha-id",
        name="alpha",
        version="0.1.0",
        manifest_path=alpha_manifest,
        root_path=alpha_dir,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )


def _create_beta_crate_with_dependencies(
    workspace_root: Path, alpha_id: str
) -> WorkspaceCrate:
    """Create the beta crate with dependency entries referencing alpha."""
    beta_dir = workspace_root / "crates" / "beta"
    beta_dir.mkdir(parents=True, exist_ok=True)
    beta_manifest = beta_dir / "Cargo.toml"
    beta_manifest.write_text(
        textwrap.dedent(
            """
            [package]
            name = "beta"
            version = "0.1.0"

            [dependencies]
            alpha = "^0.1.0"

            [dev-dependencies]
            alpha = { version = "~0.1.0", path = "../alpha" }

            [build-dependencies.alpha]
            version = "0.1.0"
            path = "../alpha"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return WorkspaceCrate(
        id="beta-id",
        name="beta",
        version="0.1.0",
        manifest_path=beta_manifest,
        root_path=beta_dir,
        publish=True,
        readme_is_workspace=False,
        dependencies=(
            WorkspaceDependency(
                package_id=alpha_id,
                name="alpha",
                manifest_name="alpha",
                kind=None,
            ),
            WorkspaceDependency(
                package_id=alpha_id,
                name="alpha",
                manifest_name="alpha",
                kind="dev",
            ),
            WorkspaceDependency(
                package_id=alpha_id,
                name="alpha",
                manifest_name="alpha",
                kind="build",
            ),
        ),
    )
