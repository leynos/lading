"""Workspace fixtures for configured non-member manifest rewrite tests."""

import dataclasses as dc
import typing as typ
from pathlib import Path

from lading import config as config_module
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.workspace_builders import _make_workspace

if typ.TYPE_CHECKING:
    from lading.workspace import WorkspaceGraph


@dc.dataclass(frozen=True, slots=True)
class _PartialWriteFailureCase:
    """Inputs and original bytes for a configured partial-write scenario."""

    root: Path
    workspace: WorkspaceGraph
    configuration: config_module.LadingConfig
    fixtures: tuple[Path, ...]
    workspace_manifests: dict[Path, bytes]
    second_fixture_original: bytes


def _make_partial_write_failure_case(root: Path) -> _PartialWriteFailureCase:
    """Create a workspace with two configured manifests and versioned paths."""
    workspace = _make_workspace(root)
    workspace_manifests = {
        root / "Cargo.toml": (root / "Cargo.toml").read_bytes(),
        **{
            crate.manifest_path: crate.manifest_path.read_bytes()
            for crate in workspace.crates
        },
    }
    fixtures = tuple(
        root / f"tests/fixtures/{name}/Cargo.toml" for name in ("first", "second")
    )
    for fixture in fixtures:
        fixture.parent.mkdir(parents=True)
        fixture.write_text(
            '[dependencies]\nalpha = "0.1.0"\n\n'
            '[patch.crates-io]\nalpha = { path = "../alpha-0.1.0" }\n',
            encoding="utf-8",
        )
    configuration = config_module.LadingConfig(
        bump=config_module.BumpConfig(
            manifest_rewrites=(
                ManifestRewriteConfig(
                    paths=tuple(path.relative_to(root).as_posix() for path in fixtures),
                    string_values=(
                        StringValueRewriteConfig(
                            table=("patch", "crates-io"), field="path"
                        ),
                    ),
                ),
            )
        )
    )
    return _PartialWriteFailureCase(
        root=root,
        workspace=workspace,
        configuration=configuration,
        fixtures=fixtures,
        workspace_manifests=workspace_manifests,
        second_fixture_original=fixtures[1].read_bytes(),
    )
