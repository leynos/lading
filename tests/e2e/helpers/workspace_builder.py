"""Workspace builder for end-to-end tests."""

from __future__ import annotations

import dataclasses as dc
import json
import textwrap
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path
else:  # pragma: no cover - runtime typing fallback
    Path = typ.Any  # type: ignore[assignment]


@dc.dataclass(frozen=True, slots=True)
class NonTrivialWorkspace:
    """Non-trivial workspace fixture metadata for E2E tests."""

    root: Path
    version: str
    crate_names: tuple[str, ...]
    cargo_metadata_payload: typ.Mapping[str, typ.Any]


def create_nontrivial_workspace(
    workspace_root: Path,
    *,
    version: str = "0.1.0",
) -> NonTrivialWorkspace:
    """Create a non-trivial Rust workspace rooted at ``workspace_root``."""
    crate_names = ("core", "utils", "app")
    crates_dir = workspace_root / "crates"
    crates_dir.mkdir(parents=True, exist_ok=True)

    _write_workspace_manifest(workspace_root, crate_names, version=version)
    _write_lading_config(workspace_root)
    _write_workspace_readme(workspace_root, crate_names, version=version)

    core_manifest = _create_crate(
        workspace_root,
        "core",
        version=version,
        manifest_extra="",
    )
    utils_manifest = _create_crate(
        workspace_root,
        "utils",
        version=version,
        manifest_extra=textwrap.dedent(
            f"""
            [dependencies]
            core = "^{version}"

            [dev-dependencies]
            core = {{ version = "~{version}", path = "../core" }}
            """
        ).strip(),
    )
    app_manifest = _create_crate(
        workspace_root,
        "app",
        version=version,
        manifest_extra=textwrap.dedent(
            f"""
            [dependencies]
            core = "{version}"
            utils = {{ version = "~{version}", path = "../utils" }}

            [build-dependencies]
            core = "{version}"
            """
        ).strip(),
    )

    metadata_payload = _build_cargo_metadata_payload(
        workspace_root,
        version=version,
        manifests={
            "core": core_manifest,
            "utils": utils_manifest,
            "app": app_manifest,
        },
    )

    return NonTrivialWorkspace(
        root=workspace_root,
        version=version,
        crate_names=crate_names,
        cargo_metadata_payload=metadata_payload,
    )


def _write_workspace_manifest(
    workspace_root: Path,
    crate_names: tuple[str, ...],
    *,
    version: str,
) -> None:
    """Write a root Cargo.toml defining the workspace members."""
    members_literal = ", ".join(f'"crates/{name}"' for name in crate_names)
    manifest_text = textwrap.dedent(
        f"""
        [workspace]
        members = [{members_literal}]

        [workspace.package]
        version = "{version}"
        """
    ).lstrip()
    (workspace_root / "Cargo.toml").write_text(manifest_text, encoding="utf-8")


def _write_lading_config(workspace_root: Path) -> None:
    """Write a representative lading.toml configuration for E2E tests."""
    config_text = textwrap.dedent(
        """
        [bump]

        [bump.documentation]
        globs = ["README.md"]

        [publish]
        strip_patches = "all"
        """
    ).lstrip()
    (workspace_root / "lading.toml").write_text(config_text, encoding="utf-8")


def _write_workspace_readme(
    workspace_root: Path,
    crate_names: tuple[str, ...],
    *,
    version: str,
) -> None:
    """Write a README containing a TOML fence listing crate versions."""
    lines = [
        "# Example workspace",
        "",
        "```toml",
        "[dependencies]",
        *[f'{name} = "{version}"' for name in crate_names],
        "```",
        "",
    ]
    (workspace_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _create_crate(
    workspace_root: Path,
    name: str,
    *,
    version: str,
    manifest_extra: str,
) -> Path:
    """Create a crate directory with a Cargo.toml and minimal lib source."""
    crate_root = workspace_root / "crates" / name
    crate_root.mkdir(parents=True, exist_ok=True)
    src_dir = crate_root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "lib.rs").write_text(
        'pub fn ping() -> &\'static str { "pong" }\n', encoding="utf-8"
    )
    manifest_lines = [
        "[package]",
        f'name = "{name}"',
        f'version = "{version}"',
        "readme.workspace = true",
        "",
    ]
    if manifest_extra:
        manifest_lines.append(manifest_extra.strip())
        manifest_lines.append("")
    manifest_path = crate_root / "Cargo.toml"
    manifest_path.write_text("\n".join(manifest_lines), encoding="utf-8")
    return manifest_path


def _build_cargo_metadata_payload(
    workspace_root: Path,
    *,
    version: str,
    manifests: dict[str, Path],
) -> typ.Mapping[str, typ.Any]:
    """Return a cargo metadata JSON payload describing the fixture workspace."""
    workspace_members = [f"{name}-id" for name in ("core", "utils", "app")]
    packages: list[dict[str, typ.Any]] = [
        {
            "name": "core",
            "version": version,
            "id": "core-id",
            "manifest_path": str(manifests["core"]),
            "dependencies": [],
            "publish": None,
        },
        {
            "name": "utils",
            "version": version,
            "id": "utils-id",
            "manifest_path": str(manifests["utils"]),
            "dependencies": [
                {"name": "core", "package": "core-id"},
                {"name": "core", "package": "core-id", "kind": "dev"},
            ],
            "publish": None,
        },
        {
            "name": "app",
            "version": version,
            "id": "app-id",
            "manifest_path": str(manifests["app"]),
            "dependencies": [
                {"name": "core", "package": "core-id"},
                {"name": "utils", "package": "utils-id"},
                {"name": "core", "package": "core-id", "kind": "build"},
            ],
            "publish": None,
        },
    ]
    payload: dict[str, typ.Any] = {
        "workspace_root": str(workspace_root),
        "packages": packages,
        "workspace_members": workspace_members,
    }
    # Ensure the payload is JSON-serialisable for cmd-mox responses.
    json.dumps(payload)
    return payload
