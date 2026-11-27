"""Shared setup for confined workspace-discovery tests.

The recording runner, workspace writer and metadata payload are used by the
discovery, member-pattern and metadata-validation suites. Keeping one copy here
gives metadata-schema changes a single implementation and avoids importing
private helpers from collected test modules.
"""

import collections.abc as cabc
import dataclasses as dc
import json
import typing as typ
from pathlib import Path

from lading.workspace.confined_discovery import load_bump_workspace

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner
    from lading.workspace import WorkspaceGraph


@dc.dataclass(slots=True)
class RecordingRunner:
    """Command runner that records invocations and returns one metadata payload."""

    payload: dict[str, typ.Any]
    calls: list[tuple[str, ...]] = dc.field(default_factory=list)

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: object | None = None,
        env: object | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record ``command`` and return the configured metadata payload."""
        del cwd, env, echo_stdout
        self.calls.append(tuple(command))
        return 0, json.dumps(self.payload), ""


@dc.dataclass(frozen=True, slots=True)
class WorkspaceShape:
    """Optional overrides for the written workspace manifest.

    Attributes
    ----------
    excludes : cabc.Sequence[str]
        Exclusion patterns added to ``[workspace].exclude``.
    workspace_table : str | None
        A complete replacement for the generated workspace table.
    """

    excludes: cabc.Sequence[str] = ()
    workspace_table: str | None = None


def write_manifest(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _member_package(root: Path, name: str) -> dict[str, typ.Any]:
    """Return a Cargo metadata package entry for the crate at ``name``."""
    crate_name = Path(name).name
    manifest = root / "crates" / name / "Cargo.toml"
    return {
        "id": f"{crate_name}-id",
        "name": crate_name,
        "version": "0.1.0",
        "manifest_path": str(manifest),
        "dependencies": [],
        "publish": None,
    }


def payload(root: Path, names: cabc.Sequence[str]) -> dict[str, typ.Any]:
    """Return a Cargo metadata payload for the named member crates."""
    return {
        "workspace_root": str(root),
        "packages": [_member_package(root, name) for name in names],
        "workspace_members": [f"{Path(name).name}-id" for name in names],
    }


def prepare_workspace(
    root: Path,
    members: cabc.Sequence[str],
    names: cabc.Sequence[str],
    *,
    options: WorkspaceShape | None = None,
) -> None:
    """Write a workspace manifest and one manifest per named relative path."""
    shape = options or WorkspaceShape()
    excludes = shape.excludes
    workspace_table = shape.workspace_table
    members_literal = ", ".join(f'"{member}"' for member in members)
    excludes_literal = ", ".join(f'"{entry}"' for entry in excludes)
    workspace = workspace_table or (
        f"[workspace]\nmembers = [{members_literal}]\n"
        + (f"exclude = [{excludes_literal}]\n" if excludes else "")
    )
    write_manifest(root / "Cargo.toml", workspace)
    for relative in names:
        crate_name = Path(relative).name
        write_manifest(
            root / "crates" / relative / "Cargo.toml",
            f'[package]\nname = "{crate_name}"\nversion = "0.1.0"\n',
        )


def load_workspace(
    root: Path,
    payload_data: dict[str, typ.Any],
) -> tuple[WorkspaceGraph, RecordingRunner]:
    """Load the workspace with a recording runner and return both."""
    runner = RecordingRunner(payload=payload_data)
    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))
    return graph, runner


def load_graph(root: Path, payload_data: dict[str, typ.Any]) -> WorkspaceGraph:
    """Load the workspace with a runner that returns ``payload_data``."""
    graph, _runner = load_workspace(root, payload_data)
    return graph
