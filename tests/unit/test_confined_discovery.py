"""Unit tests for confined, read-only bump workspace discovery.

Every rejection test places a valid member beside the invalid one and asserts
that Cargo was not invoked when the rejection is lexically decidable, so the
pre-Cargo contract is exercised rather than assumed.
"""

import collections.abc as cabc
import dataclasses as dc
import json
import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import (
    BumpPreflightError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from lading.workspace import WorkspaceModelError
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


def _write_manifest(path: Path, text: str) -> Path:
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


def _payload(root: Path, names: cabc.Sequence[str]) -> dict[str, typ.Any]:
    """Return a Cargo metadata payload for the named member crates."""
    return {
        "workspace_root": str(root),
        "packages": [_member_package(root, name) for name in names],
        "workspace_members": [f"{Path(name).name}-id" for name in names],
    }


def _prepare_workspace(
    root: Path,
    members: cabc.Sequence[str],
    names: cabc.Sequence[str],
    *,
    excludes: cabc.Sequence[str] = (),
    workspace_table: str | None = None,
) -> None:
    """Write a workspace manifest and one manifest per named relative path."""
    members_literal = ", ".join(f'"{member}"' for member in members)
    excludes_literal = ", ".join(f'"{entry}"' for entry in excludes)
    workspace = workspace_table or (
        f"[workspace]\nmembers = [{members_literal}]\n"
        + (f"exclude = [{excludes_literal}]\n" if excludes else "")
    )
    _write_manifest(root / "Cargo.toml", workspace)
    for relative in names:
        crate_name = Path(relative).name
        _write_manifest(
            root / "crates" / relative / "Cargo.toml",
            f'[package]\nname = "{crate_name}"\nversion = "0.1.0"\n',
        )


def _load(root: Path, payload: dict[str, typ.Any]) -> tuple[typ.Any, _RecordingRunner]:
    """Load the workspace with a recording runner and return both."""
    runner = _RecordingRunner(payload=payload)
    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))
    return graph, runner


def test_load_bump_workspace_uses_no_deps(tmp_path: Path) -> None:
    """Discovery asks Cargo for the workspace only, with no dependency graph."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    graph, runner = _load(tmp_path, _payload(tmp_path, ["alpha"]))
    assert runner.calls == [
        ("cargo", "metadata", "--format-version", "1", "--no-deps")
    ], "bump discovery must pass --no-deps so metadata stays read-only"
    assert [crate.name for crate in graph.crates] == ["alpha"], (
        "the graph must contain the reported member"
    )
    assert graph.workspace_root == tmp_path.resolve(), (
        "the graph root must be the canonical workspace root"
    )


def test_load_bump_workspace_rejects_a_missing_literal_member(
    tmp_path: Path,
) -> None:
    """Reject a literal member pattern that names no directory, before Cargo."""
    _prepare_workspace(tmp_path, ["crates/alpha", "crates/missing"], ["alpha"])
    runner = _RecordingRunner(payload=_payload(tmp_path, ["alpha"]))
    with pytest.raises(MissingBumpTargetError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    message = str(excinfo.value)
    assert "workspace.members" in message, "the diagnostic must name the manifest field"
    assert "missing" in message, "the diagnostic must name the rejected member"
    assert not runner.calls, (
        "a missing literal member must be rejected before any Cargo invocation"
    )


def test_load_bump_workspace_rejects_an_unmatched_member_glob(
    tmp_path: Path,
) -> None:
    """Reject a member glob that matches nothing, naming the pattern."""
    _prepare_workspace(tmp_path, ["crates/alpha", "missing/*"], ["alpha"])
    runner = _RecordingRunner(payload=_payload(tmp_path, ["alpha"]))
    with pytest.raises(MissingBumpTargetError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    assert "missing/*" in str(excinfo.value), (
        "the diagnostic must name the unmatched member glob"
    )
    assert not runner.calls, "an unmatched glob must be rejected before Cargo"


def test_load_bump_workspace_rejects_a_member_without_a_manifest(
    tmp_path: Path,
) -> None:
    """Reject a matched member directory with no regular Cargo.toml."""
    _prepare_workspace(tmp_path, ["crates/*", "crates/empty"], ["alpha"])
    (tmp_path / "crates" / "empty").mkdir()
    runner = _RecordingRunner(payload=_payload(tmp_path, ["alpha"]))
    with pytest.raises(MissingBumpTargetError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    message = str(excinfo.value)
    assert "crates/empty" in message, (
        "the diagnostic must name the member directory without a manifest"
    )
    assert "crates/*" in message, "the diagnostic must name the matched pattern"


def test_load_bump_workspace_applies_exclusions(tmp_path: Path) -> None:
    """An excluded glob match is removed before Cargo's report is cross-checked."""
    _prepare_workspace(
        tmp_path,
        ["crates/*"],
        ["alpha", "beta"],
        excludes=["crates/beta"],
    )
    graph, runner = _load(tmp_path, _payload(tmp_path, ["alpha"]))
    assert runner.calls, "the metadata call must still run"
    assert {crate.name for crate in graph.crates} == {"alpha"}, (
        "the excluded glob match must not be required from Cargo's report"
    )


def test_load_bump_workspace_excludes_descendants_of_a_glob(tmp_path: Path) -> None:
    """An excluded directory removes glob matches beneath it, as Cargo does."""
    _prepare_workspace(
        tmp_path,
        ["crates/*/app"],
        ["retired/app", "live/app"],
        excludes=["crates/retired"],
    )
    graph, _runner = _load(tmp_path, _payload(tmp_path, ["live/app"]))
    assert len(graph.crates) == 1, (
        "only the live crate's glob match may remain a member"
    )
    assert (
        graph.crates[0].root_path == tmp_path.resolve() / "crates" / "live" / "app"
    ), "the remaining member must be the live crate, not the retired one"


def test_load_bump_workspace_accepts_a_virtual_workspace(tmp_path: Path) -> None:
    """Accept a virtual manifest with no root package."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha"])
    graph, _runner = _load(tmp_path, _payload(tmp_path, ["alpha"]))
    assert [crate.name for crate in graph.crates] == ["alpha"], (
        "a virtual workspace must build a graph from its members"
    )


def test_load_bump_workspace_accepts_a_root_package(tmp_path: Path) -> None:
    """Accept a single-package workspace whose root is the only member."""
    _write_manifest(
        tmp_path / "Cargo.toml",
        '[package]\nname = "root"\nversion = "0.1.0"\n',
    )
    payload = {
        "workspace_root": str(tmp_path),
        "packages": [
            {
                "id": "root-id",
                "name": "root",
                "version": "0.1.0",
                "manifest_path": str(tmp_path / "Cargo.toml"),
                "dependencies": [],
                "publish": None,
            }
        ],
        "workspace_members": ["root-id"],
    }
    graph, _runner = _load(tmp_path, payload)
    assert [crate.name for crate in graph.crates] == ["root"], (
        "a root-package workspace must expose the root as a member"
    )


def test_load_bump_workspace_allows_implicit_members(tmp_path: Path) -> None:
    """Allow a Cargo-reported member that no declared pattern names."""
    _prepare_workspace(tmp_path, ["crates/alpha"], ["alpha", "implicit"])
    # The implicit member is reached as a path dependency of alpha, so Cargo
    # reports it without the manifest declaring it.
    graph, _runner = _load(tmp_path, _payload(tmp_path, ["alpha", "implicit"]))
    assert {crate.name for crate in graph.crates} == {"alpha", "implicit"}, (
        "a validated implicit member must not be rejected as an extra"
    )


def test_load_bump_workspace_rejects_a_member_cargo_omits(tmp_path: Path) -> None:
    """Reject a declared member that Cargo does not report."""
    _prepare_workspace(tmp_path, ["crates/alpha", "crates/beta"], ["alpha", "beta"])
    runner = _RecordingRunner(payload=_payload(tmp_path, ["alpha"]))
    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(tmp_path, runner=typ.cast("CommandRunner", runner))
    assert "crates/beta" in str(excinfo.value), (
        "the diagnostic must name the declared member Cargo did not report"
    )


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


def test_load_bump_workspace_rejects_a_symlinked_member_before_cargo(
    tmp_path: Path,
) -> None:
    """Reject a symlinked member directory without invoking Cargo."""
    outside = tmp_path / "outside"
    root = tmp_path / "workspace"
    outside.mkdir()
    (outside / "Cargo.toml").write_text(
        '[package]\nname = "outside"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    root.mkdir()
    (root / "crates").symlink_to(outside, target_is_directory=True)
    _write_manifest(
        root / "Cargo.toml",
        '[workspace]\nmembers = ["crates/linked"]\n',
    )
    runner = _RecordingRunner(payload={"workspace_root": str(root)})
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))
    assert "symlink" in str(excinfo.value), (
        "a symlinked member component must be named as a symlink"
    )
    assert not runner.calls, "a lexically rejected member must not reach Cargo"


def test_load_bump_workspace_rejects_a_symlinked_declared_manifest(
    tmp_path: Path,
) -> None:
    """Reject a declared member whose manifest is a final symlink, before Cargo."""
    outside = tmp_path / "outside"
    root = tmp_path / "workspace"
    outside.mkdir()
    (outside / "Cargo.toml").write_text(
        '[package]\nname = "outside"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "crates" / "alpha").mkdir(parents=True)
    (root / "crates" / "alpha" / "Cargo.toml").symlink_to(outside / "Cargo.toml")
    _write_manifest(root / "Cargo.toml", '[workspace]\nmembers = ["crates/alpha"]\n')
    payload = _payload(root, ["alpha"])
    runner = _RecordingRunner(payload=payload)
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))
    assert "symlink" in str(excinfo.value), (
        "a declared manifest reached through a symlink must be rejected"
    )
    assert not runner.calls, (
        "a declared member's symlinked manifest is rejected before Cargo runs"
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
