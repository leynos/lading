"""Unit tests for lockfile destination planning before a bump mutation.

The planner validates configured and discovered manifests, asks Cargo for each
manifest's effective workspace root, and returns an immutable plan. These tests
use a recording command runner so every Git and Cargo invocation is visible.
"""

import collections.abc as cabc
import dataclasses as dc
import json
from pathlib import Path

import pytest

from lading.commands.bump_lockfile_paths import LockfileRegenerationError
from lading.commands.bump_lockfile_planning import LockfilePlan, plan_lockfiles
from lading.commands.bump_lockfile_regeneration import regenerate_planned
from lading.utils.confinement import (
    BumpPreflightError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)


@dc.dataclass(slots=True)
class _RecordingRunner:
    """Record Git and Cargo invocations and answer from a fixed payload."""

    metadata_root: Path
    calls: list[tuple[tuple[str, ...], Path | None]] = dc.field(default_factory=list)

    def __call__(
        self,
        command: cabc.Sequence[str],
        *,
        cwd: Path | None = None,
        env: cabc.Mapping[str, str] | None = None,
        echo_stdout: bool = True,
    ) -> tuple[int, str, str]:
        """Record the command and return a canned response."""
        del env, echo_stdout
        self.calls.append((tuple(command), cwd))
        if command[0] == "git":
            return 0, "", ""
        if command[0] == "cargo" and command[1] == "metadata":
            payload = {"workspace_root": str(self.metadata_root)}
            return 0, json.dumps(payload), ""
        if command[0] == "cargo" and command[1] == "update":
            return 0, "", ""
        return 1, "", f"unexpected command: {command}"

    def cargo_metadata_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded ``cargo metadata`` argument tuples."""
        return [
            command
            for command, _cwd in self.calls
            if command[:2] == ("cargo", "metadata")
        ]

    def cargo_update_calls(self) -> list[tuple[str, ...]]:
        """Return the recorded ``cargo update`` argument tuples."""
        return [
            command
            for command, _cwd in self.calls
            if command[:2] == ("cargo", "update")
        ]


def _write_workspace(root: Path, *, members: cabc.Sequence[str] = ()) -> Path:
    """Write a root manifest and return it."""
    members_literal = ", ".join(f'"{member}"' for member in members)
    manifest = root / "Cargo.toml"
    manifest.write_text(
        f"[workspace]\nmembers = [{members_literal}]\n\n"
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return manifest


def _write_package(root: Path, relative: str, *, standalone: bool) -> Path:
    """Write a package manifest and return it."""
    manifest = root / relative
    manifest.parent.mkdir(parents=True, exist_ok=True)
    suffix = "\n[workspace]\n" if standalone else ""
    manifest.write_text(
        f'[package]\nname = "nested"\nversion = "0.1.0"\n{suffix}',
        encoding="utf-8",
    )
    return manifest


def test_root_only_plan_makes_no_metadata_query(tmp_path: Path) -> None:
    """A workspace with no configured manifests plans only the root lockfile."""
    root = tmp_path.resolve()
    _write_workspace(root)
    runner = _RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, (), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "the workspace root lockfile must be the only destination"
    )
    assert runner.cargo_metadata_calls() == [], (
        "the root manifest needs no Cargo query to learn its workspace root"
    )


def test_nested_standalone_manifest_keeps_its_own_lockfile(tmp_path: Path) -> None:
    """A nested standalone workspace contributes its own lockfile."""
    root = tmp_path.resolve()
    _write_workspace(root)
    nested = _write_package(root, "fixtures/nested/Cargo.toml", standalone=True)
    runner = _RecordingRunner(metadata_root=nested.parent)

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
            str(nested),
        )
    ]
    assert runner.cargo_metadata_calls() == expected_metadata_call, (
        "the plan must ask Cargo for the nested manifest's workspace root"
    )


def test_member_manifest_deduplicates_to_the_outer_lockfile(tmp_path: Path) -> None:
    """A manifest Cargo reports inside the outer workspace shares its lockfile."""
    root = tmp_path.resolve()
    _write_workspace(root, members=("crates/app",))
    member = _write_package(root, "crates/app/Cargo.toml", standalone=False)
    runner = _RecordingRunner(metadata_root=root)

    plan = plan_lockfiles(root, ("crates/app/Cargo.toml",), runner=runner, environ={})

    assert plan.lockfiles == (root / "Cargo.lock",), (
        "a member manifest must not introduce an adjacent lockfile"
    )
    assert plan.manifests == (root / "Cargo.toml",), (
        "the effective workspace root must be updated once"
    )
    assert str(member) in runner.cargo_metadata_calls()[0], (
        "the query must name the configured member manifest"
    )


def test_plan_rejects_the_lockfile_path_override(tmp_path: Path) -> None:
    """A Cargo lockfile-path override is rejected before any invocation."""
    root = tmp_path.resolve()
    _write_workspace(root)
    runner = _RecordingRunner(metadata_root=root)

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
    _write_workspace(root)
    runner = _RecordingRunner(metadata_root=root)

    with pytest.raises(UnsafeBumpTargetError):
        plan_lockfiles(root, (configured,), runner=runner, environ={})

    assert runner.cargo_metadata_calls() == [], (
        "an unsafe manifest must be rejected before Cargo is asked about it"
    )


def test_plan_rejects_a_manifest_not_named_cargo_toml(tmp_path: Path) -> None:
    """A configured manifest must be a Cargo.toml file."""
    root = tmp_path.resolve()
    _write_workspace(root)
    _write_package(root, "fixtures/nested/other.toml", standalone=True)
    runner = _RecordingRunner(metadata_root=root)

    with pytest.raises(LockfileRegenerationError) as excinfo:
        plan_lockfiles(root, ("fixtures/nested/other.toml",), runner=runner, environ={})

    assert "Cargo.toml" in str(excinfo.value), (
        "the diagnostic must state the required basename"
    )


def test_plan_rejects_a_missing_configured_manifest(tmp_path: Path) -> None:
    """A configured manifest that does not exist is a preflight error."""
    root = tmp_path.resolve()
    _write_workspace(root)
    runner = _RecordingRunner(metadata_root=root)

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
    _write_workspace(root)
    outside = tmp_path / "outside.lock"
    outside.write_text("external\n", encoding="utf-8")
    (root / "Cargo.lock").symlink_to(outside)
    runner = _RecordingRunner(metadata_root=root)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        plan_lockfiles(root, (), runner=runner, environ={})

    assert "symlink" in str(excinfo.value), (
        "a symlinked lockfile destination must be rejected as a symlink"
    )
    assert outside.read_text(encoding="utf-8") == "external\n", (
        "planning must not modify the external sentinel"
    )


def test_regenerate_planned_runs_each_effective_root(tmp_path: Path) -> None:
    """Regeneration consumes the plan without rediscovering manifests."""
    root = tmp_path.resolve()
    _write_workspace(root)
    nested = _write_package(root, "fixtures/nested/Cargo.toml", standalone=True)
    runner = _RecordingRunner(metadata_root=root)
    nested_plan = LockfilePlan(
        root=root,
        manifests=(root / "Cargo.toml", nested),
        lockfiles=(root / "Cargo.lock", nested.parent / "Cargo.lock"),
    )

    lockfiles = regenerate_planned(nested_plan, runner=runner)

    assert lockfiles == nested_plan.lockfiles, (
        "regeneration must return the validated destinations"
    )
    expected_updates = [
        ("cargo", "update", "--workspace", "--manifest-path", str(root / "Cargo.toml")),
        ("cargo", "update", "--workspace", "--manifest-path", str(nested)),
    ]
    assert runner.cargo_update_calls() == expected_updates, (
        "one update must run per effective workspace root"
    )
