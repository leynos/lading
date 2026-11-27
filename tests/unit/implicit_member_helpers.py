"""Shared fixtures for implicit-member discovery tests.

The workspace builders, metadata payload and recording runner are used by both
the filesystem-rejection suite and the dependency-shape suite, so they live
here rather than being copied between them.
"""

import collections.abc as cabc
import json
import os
import typing as typ
from pathlib import Path

import pytest

from lading.workspace.confined_discovery import load_bump_workspace
from tests.helpers.recording_runner import RecordingCommandRunner

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceGraph


def write_workspace(root: Path, *, dependency: str = "../dep") -> None:
    """Write a root workspace and an app that path-depends on ``dependency``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n', encoding="utf-8"
    )
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        f"[dependencies]\ndep = {{ path = {json.dumps(dependency)} }}\n",
        encoding="utf-8",
    )


def write_package(directory: Path, name: str) -> Path:
    """Write one package manifest and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return manifest


def payload(root: Path, names: cabc.Sequence[str]) -> dict[str, typ.Any]:
    """Return a metadata payload for packages under ``root``."""
    packages = [
        {
            "id": f"{name}-id",
            "name": name,
            "version": "0.1.0",
            "manifest_path": str(root / name / "Cargo.toml"),
            "dependencies": [],
            "publish": None,
        }
        for name in names
    ]
    return {
        "workspace_root": str(root),
        "packages": packages,
        "workspace_members": [f"{name}-id" for name in names],
    }


def runner_for(payload_data: dict[str, typ.Any]) -> RecordingCommandRunner:
    """Return a recording runner serving ``payload_data``."""
    return RecordingCommandRunner(metadata=payload_data)


def load(root: Path, payload_data: dict[str, typ.Any]) -> WorkspaceGraph:
    """Load the workspace through a recording runner."""
    runner = runner_for(payload_data)
    return load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))


def make_fifo(path: Path) -> None:
    """Create a FIFO at ``path``, skipping when the host cannot."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkfifo(path)
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")
