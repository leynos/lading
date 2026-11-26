"""Shared setup for configured manifest rewrite recovery tests."""

import dataclasses as dc
import typing as typ
from unittest import mock

import pytest

from lading import config as config_module
from lading.commands import (
    bump,
    bump_manifest_rewrite_application,
    bump_manifest_rewrites,
    bump_pipeline_manifest_rewrites,
    bump_toml_files,
)
from lading.commands.bump_manifest_rewrites import ManifestRewriteError
from lading.config import ManifestRewriteConfig, StringValueRewriteConfig
from tests.helpers.workspace_builders import _make_workspace

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from lading.commands.bump_toml_write_options import (
        AtomicWriteOptions,
        AtomicWriteStatus,
    )
    from lading.workspace import WorkspaceGraph


class _InjectedMemberManifestError(Exception):
    """Specific failure used to stop a bump after an earlier member write."""


class _InjectedFixtureWriteError(OSError):
    """Specific fixture-write failure used to exercise rollback handling."""

    def __init__(self) -> None:
        """Set a stable message for the injected I/O failure."""
        super().__init__("injected second fixture write failure")


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


def _second_fixture_write_failure(
    case: _PartialWriteFailureCase,
    real_write: cabc.Callable[..., None],
    *,
    before_failure: cabc.Callable[[Path], None] | None = None,
) -> cabc.Callable[..., None]:
    """Build a writer that forwards fixture one and fails fixture two."""

    def fail_second_fixture(
        path: Path,
        content: str,
        write_options: AtomicWriteOptions | None = None,
    ) -> None:
        """Inject the selected failure while forwarding earlier writes."""
        if path == case.fixtures[1].resolve():
            if before_failure is not None:
                before_failure(path)
            raise _InjectedFixtureWriteError
        real_write(
            path,
            content,
            write_options,
        )

    return fail_second_fixture


def _run_partial_write_failure(
    case: _PartialWriteFailureCase,
    *,
    before_failure: cabc.Callable[[Path], None] | None = None,
) -> ManifestRewriteError:
    """Run a bump whose second fixture write fails after fixture one."""
    if not bump_toml_files._supports_bounded_directory_operations():
        pytest.skip("workspace-bounded atomic writes are unavailable")
    write_atomic_text = bump_manifest_rewrite_application.write_atomic_text
    failure_injector = _second_fixture_write_failure(
        case, write_atomic_text, before_failure=before_failure
    )
    with (
        mock.patch.object(
            bump_manifest_rewrite_application,
            "write_atomic_text",
            side_effect=failure_injector,
        ),
        pytest.raises(ManifestRewriteError) as failure,
    ):
        bump.run(
            case.root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=False,
                configuration=case.configuration,
                workspace=case.workspace,
            ),
        )
    return failure.value


def _pending_recovery_stage(
    manifest_path: Path,
    original_text: str,
    pending_text: str,
    status: AtomicWriteStatus,
) -> bump_pipeline_manifest_rewrites._ManifestRewriteStage:
    """Build the stage entry left by a replacement that preceded its callback."""
    plan = bump_manifest_rewrites.ManifestRewritePlan(
        manifest_path, original_text, pending_text
    )
    return bump_pipeline_manifest_rewrites._ManifestRewriteStage(
        workspace_root=manifest_path.parent,
        plans=(plan,),
        rollback_baselines={manifest_path: original_text},
        pending_manifest_contents={manifest_path: pending_text},
        pending_write_statuses={manifest_path: status},
        written_manifest_destinations={manifest_path: manifest_path},
    )
