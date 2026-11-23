"""Unit tests for Cargo metadata validation in bump discovery.

The loader validates the metadata payload and every reported member manifest
before the graph is built; these examples exercise the rejection paths.
"""

import collections.abc as cabc
import dataclasses as dc
import json
import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    UnsafeBumpTargetError,
)
from lading.workspace import WorkspaceGraph, WorkspaceModelError
from lading.workspace.confined_discovery import load_bump_workspace

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner


@dc.dataclass(slots=True)
class _RecordingRunner:
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


def _load(root: Path, payload: dict[str, typ.Any]) -> WorkspaceGraph:
    """Load the workspace with a runner that returns ``payload``."""
    runner = _RecordingRunner(payload=payload)
    return load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))


def _prepare_workspace(
    root: Path,
    members: cabc.Sequence[str],
    names: cabc.Sequence[str],
    *,
    excludes: cabc.Sequence[str] = (),
) -> None:
    """Write a workspace manifest and one manifest per named crate."""
    root.mkdir(parents=True, exist_ok=True)
    members_literal = ", ".join(f'"{member}"' for member in members)
    excludes_literal = ", ".join(f'"{entry}"' for entry in excludes)
    root.joinpath("Cargo.toml").write_text(
        f"[workspace]\nmembers = [{members_literal}]\n"
        + (f"exclude = [{excludes_literal}]\n" if excludes else ""),
        encoding="utf-8",
    )
    for name in names:
        manifest = root / "crates" / name / "Cargo.toml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )


def _payload(root: Path, names: cabc.Sequence[str]) -> dict[str, typ.Any]:
    """Return a valid metadata payload for the named crates."""
    packages = []
    members = []
    for name in names:
        manifest = root / "crates" / name / "Cargo.toml"
        packages.append({
            "id": f"{name}-id",
            "name": name,
            "version": "0.1.0",
            "manifest_path": str(manifest),
            "dependencies": [],
            "publish": None,
        })
        members.append(f"{name}-id")
    return {
        "workspace_root": str(root),
        "packages": packages,
        "workspace_members": members,
    }


def test_load_bump_workspace_rejects_a_foreign_workspace_root(
    tmp_path: Path,
) -> None:
    """Require Cargo's workspace root to equal the requested canonical root."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["workspace_root"] = str(tmp_path / "elsewhere")
    runner = _RecordingRunner(payload=payload)
    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    message = str(excinfo.value)
    assert "--workspace-root" in message, (
        "the diagnostic must suggest the workspace-root override"
    )


def test_load_bump_workspace_rejects_a_symlinked_implicit_manifest(
    tmp_path: Path,
) -> None:
    """Reject a Cargo-reported implicit member whose manifest is a symlink."""
    outside = tmp_path / "outside"
    root = tmp_path / "workspace"
    outside.mkdir()
    (outside / "Cargo.toml").write_text(
        '[package]\nname = "outside"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    _prepare_workspace(root, ["crates/alpha"], ["alpha"])
    (root / "crates" / "implicit").mkdir()
    (root / "crates" / "implicit" / "Cargo.toml").symlink_to(outside / "Cargo.toml")
    # The implicit member is not declared, so only Cargo's report names it.
    payload = _payload(root, ["alpha", "implicit"])
    runner = _RecordingRunner(payload=payload)
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))
    assert "symlink" in str(excinfo.value), (
        "an implicit member reached through a symlink must be rejected"
    )
    assert runner.calls, (
        "an implicit member is only known after Cargo reports the member set"
    )


def test_load_bump_workspace_rejects_a_missing_member_identifier(
    tmp_path: Path,
) -> None:
    """Preserve ``WorkspaceModelError`` when a member id is absent."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["workspace_members"] = ["ghost-id"]
    runner = _RecordingRunner(payload=payload)
    with pytest.raises(WorkspaceModelError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    assert "ghost-id" in str(excinfo.value), (
        "the model error must name the missing member identifier"
    )


def test_load_bump_workspace_rejects_an_invalid_root_manifest(
    tmp_path: Path,
) -> None:
    """A root manifest that is not valid TOML is rejected before Cargo runs."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text("not toml [\n", encoding="utf-8")
    runner = _RecordingRunner(payload={})

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "not valid TOML" in str(excinfo.value), (
        "the diagnostic must name the malformed root manifest"
    )
    assert not runner.calls, (
        "an invalid root manifest must be rejected before Cargo runs"
    )


def test_load_bump_workspace_rejects_an_undecodable_root_manifest(
    tmp_path: Path,
) -> None:
    """A root manifest that is not valid UTF-8 is an examination error."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_bytes(b"\xff\xfe")
    runner = _RecordingRunner(payload={})

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "cannot read" in str(excinfo.value), (
        "the diagnostic must name the unreadable root manifest"
    )


def test_load_bump_workspace_rejects_a_non_array_members_table(
    tmp_path: Path,
) -> None:
    """A ``workspace.members`` value that is not an array is rejected."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = "crates/alpha"\n', encoding="utf-8"
    )
    runner = _RecordingRunner(payload={})

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "members" in str(excinfo.value), "the diagnostic must name the members table"


def test_load_bump_workspace_rejects_metadata_without_a_root(
    tmp_path: Path,
) -> None:
    """Metadata that omits ``workspace_root`` cannot anchor confinement."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    del payload["workspace_root"]
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "did not report a workspace_root path" in str(excinfo.value), (
        "the diagnostic must say the root is missing"
    )


def test_load_bump_workspace_rejects_a_non_string_package_id(
    tmp_path: Path,
) -> None:
    """A package identifier that is not a string is rejected."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["packages"][0]["id"] = 42
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "package id must be a string" in str(excinfo.value), (
        "the diagnostic must name the malformed package identifier"
    )


def test_load_bump_workspace_rejects_a_non_string_member_id(
    tmp_path: Path,
) -> None:
    """A workspace member identifier that is not a string is rejected."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["workspace_members"] = [42]
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "workspace_members entries must be strings" in str(excinfo.value), (
        "the diagnostic must name the malformed member identifier"
    )


def test_load_bump_workspace_rejects_a_non_sequence_package_list(
    tmp_path: Path,
) -> None:
    """A ``packages`` value that is not a sequence is rejected."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["packages"] = "nope"
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "packages must be a sequence" in str(excinfo.value), (
        "the diagnostic must name the malformed package list"
    )


def test_load_bump_workspace_rejects_a_non_mapping_package(
    tmp_path: Path,
) -> None:
    """A package entry that is not a mapping is rejected."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["packages"] = [42]
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "packages[] entries must be mappings" in str(excinfo.value), (
        "the diagnostic must name the malformed package entry"
    )


def test_load_bump_workspace_rejects_a_non_path_manifest(
    tmp_path: Path,
) -> None:
    """A member ``manifest_path`` that is not a path is a model error."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["packages"][0]["manifest_path"] = 42
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(WorkspaceModelError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "manifest_path must be a path" in str(excinfo.value), (
        "the diagnostic must name the malformed manifest path"
    )


def test_load_bump_workspace_rejects_a_misnamed_member_manifest(
    tmp_path: Path,
) -> None:
    """A reported member manifest not named ``Cargo.toml`` is rejected."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    payload = _payload(tmp_path, ["alpha"])
    payload["packages"][0]["manifest_path"] = str(
        tmp_path / "crates" / "alpha" / "Other.toml"
    )
    runner = _RecordingRunner(payload=payload)

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))

    assert "must be named Cargo.toml" in str(excinfo.value), (
        "the diagnostic must name the expected manifest filename"
    )
