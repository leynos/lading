"""Helper utilities for constructing behavioural test data."""

from __future__ import annotations

import textwrap
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


def _create_test_crate(
    workspace_dir: Path,
    crate_name: str,
    version: str,
    dependencies_toml: str = "",
) -> Path:
    """Create a crate manifest under ``workspace_dir`` for behavioural fixtures."""
    crate_dir = workspace_dir / "crates" / crate_name
    crate_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = crate_dir / "Cargo.toml"

    sections = [
        textwrap.dedent(
            f"""
            [package]
            name = "{crate_name}"
            version = "{version}"
            """
        ).strip()
    ]
    if dependencies_toml:
        sections.append(textwrap.dedent(dependencies_toml).strip())

    manifest_path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    return manifest_path


def _build_package_metadata(
    name: str,
    manifest_path: Path,
    version: str = "0.1.0",
    **metadata: typ.Any,  # noqa: ANN401 - fixtures accept arbitrary metadata fields
) -> dict[str, typ.Any]:
    """Construct the minimal package metadata payload for ``cargo metadata``."""
    dependencies = metadata.get("dependencies")
    publish = metadata.get("publish")
    return {
        "name": name,
        "version": version,
        "id": f"{name}-id",
        "manifest_path": str(manifest_path),
        "dependencies": [] if dependencies is None else dependencies,
        "publish": publish,
    }
