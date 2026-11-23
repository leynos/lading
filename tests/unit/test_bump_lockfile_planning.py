"""Unit tests for lockfile destination planning before a bump mutation.

The planner validates configured and discovered manifests, asks Cargo for each
manifest's effective workspace root, and returns an immutable plan. These tests
use a recording command runner so every Git and Cargo invocation is visible.
"""

from pathlib import Path

import pytest

from lading.commands.bump_lockfile_paths import LockfileRegenerationError
from lading.commands.bump_lockfile_planning import plan_lockfiles
from lading.utils.confinement import (
    BumpPreflightError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from tests.unit.lockfile_planning_helpers import (
    RecordingRunner,
    write_package,
    write_workspace,
)


def test_root_only_plan_confirms_cargo_workspace_root(tmp_path: Path) -> None:
    """A workspace with no configured manifests plans only the root lockfile."""
    root = tmp_path.resolve()
    write_workspace(root)
    runner = RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, (), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "the workspace root lockfile must be the only destination"
    )
    expected_query = [
        (
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--no-deps",
            "--manifest-path",
            str(root / "Cargo.toml"),
        )
    ]
    assert runner.cargo_metadata_calls() == expected_query, (
        "the root manifest must be confirmed as Cargo's workspace root"
    )


def test_plan_rejects_a_root_inside_an_outer_workspace(tmp_path: Path) -> None:
    """A requested root that is only an outer member is rejected."""
    root = tmp_path.resolve() / "crates" / "app"
    root.mkdir(parents=True)
    write_workspace(root)
    outer = tmp_path.resolve()
    runner = RecordingRunner(
        metadata_root=outer,
        roots_by_manifest={root / "Cargo.toml": outer},
    )

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert str(outer) in str(excinfo.value), (
        "the diagnostic must suggest Cargo's reported workspace root"
    )


def test_nested_standalone_manifest_keeps_its_own_lockfile(tmp_path: Path) -> None:
    """A nested standalone workspace contributes its own lockfile."""
    root = tmp_path.resolve()
    write_workspace(root)
    nested = write_package(root, "fixtures/nested/Cargo.toml", standalone=True)
    runner = RecordingRunner(metadata_root=nested.parent)

    plan = plan_lockfiles(
        root, ("fixtures/nested/Cargo.toml",), runner=runner, environ={}
    )

    assert plan.lockfiles == (root / "Cargo.lock", nested.parent / "Cargo.lock"), (
        "the nested workspace lockfile must follow the root lockfile"
    )
    assert plan.manifests == (root / "Cargo.toml", nested), (
        "regeneration must run for each effective workspace root"
    )
    expected_metadata_call = [
        (
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--no-deps",
            "--manifest-path",
            str(root / "Cargo.toml"),
        ),
        (
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--no-deps",
            "--manifest-path",
            str(nested),
        ),
    ]
    assert runner.cargo_metadata_calls() == expected_metadata_call, (
        "the plan must ask Cargo for the nested manifest's workspace root"
    )


def test_member_manifest_deduplicates_to_the_outer_lockfile(tmp_path: Path) -> None:
    """A manifest Cargo reports inside the outer workspace shares its lockfile."""
    root = tmp_path.resolve()
    write_workspace(root, members=("crates/app",))
    member = write_package(root, "crates/app/Cargo.toml", standalone=False)
    runner = RecordingRunner(
        metadata_root=root,
        roots_by_manifest={root / "Cargo.toml": root, member: root},
    )

    plan = plan_lockfiles(root, ("crates/app/Cargo.toml",), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "a member manifest must not introduce an adjacent lockfile"
    )
    assert plan.manifests == (root / "Cargo.toml",), (
        "the effective workspace root must be updated once"
    )
    queried_manifests = [
        command[command.index("--manifest-path") + 1]
        for command in runner.cargo_metadata_calls()
    ]
    assert str(member) in queried_manifests, (
        "the query must name the configured member manifest"
    )


def test_plan_rejects_the_lockfile_path_override(tmp_path: Path) -> None:
    """A Cargo lockfile-path override is rejected before any invocation."""
    root = tmp_path.resolve()
    write_workspace(root)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(BumpPreflightError) as excinfo:
        plan_lockfiles(
            root,
            (),
            runner=runner,
            environ={"CARGO_RESOLVER_LOCKFILE_PATH": "/elsewhere/Cargo.lock"},
        )

    assert "CARGO_RESOLVER_LOCKFILE_PATH" in str(excinfo.value), (
        "the diagnostic must name the rejected environment variable"
    )
    assert not runner.calls, "the override must be rejected before Git or Cargo"


@pytest.mark.parametrize(
    "configured",
    [
        pytest.param("../outside/Cargo.toml", id="parent-traversal"),
        pytest.param("Cargo.toml/../Cargo.toml", id="embedded-traversal"),
    ],
)
def test_plan_rejects_an_escaping_configured_manifest(
    tmp_path: Path, configured: str
) -> None:
    """An unsafe configured manifest is rejected before any Cargo query."""
    root = tmp_path.resolve()
    write_workspace(root)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(UnsafeBumpTargetError):
        plan_lockfiles(root, (configured,), runner=runner, environ={})

    assert runner.cargo_metadata_calls() == [], (
        "an unsafe manifest must be rejected before Cargo is asked about it"
    )


def test_plan_rejects_a_manifest_not_named_cargo_toml(tmp_path: Path) -> None:
    """A configured manifest must be a Cargo.toml file."""
    root = tmp_path.resolve()
    write_workspace(root)
    write_package(root, "fixtures/nested/other.toml", standalone=True)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(LockfileRegenerationError) as excinfo:
        plan_lockfiles(root, ("fixtures/nested/other.toml",), runner=runner, environ={})

    assert "Cargo.toml" in str(excinfo.value), (
        "the diagnostic must state the required basename"
    )


def test_plan_rejects_a_missing_configured_manifest(tmp_path: Path) -> None:
    """A configured manifest that does not exist is a preflight error."""
    root = tmp_path.resolve()
    write_workspace(root)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(MissingBumpTargetError) as excinfo:
        plan_lockfiles(
            root, ("fixtures/missing/Cargo.toml",), runner=runner, environ={}
        )

    assert "bump.lockfile_manifests" in str(excinfo.value), (
        "the diagnostic must name the configuration field"
    )


def test_plan_rejects_a_symlinked_lockfile_destination(tmp_path: Path) -> None:
    """An existing symlinked Cargo.lock is rejected without touching its target."""
    root = tmp_path.resolve()
    write_workspace(root)
    outside = tmp_path / "outside.lock"
    outside.write_text("external\n", encoding="utf-8")
    (root / "Cargo.lock").symlink_to(outside)
    runner = RecordingRunner(metadata_root=root)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert "symlink" in str(excinfo.value), (
        "a symlinked lockfile destination must be rejected as a symlink"
    )
    assert outside.read_text(encoding="utf-8") == "external\n", (
        "planning must not modify the external sentinel"
    )
