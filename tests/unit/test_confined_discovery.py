"""Unit tests for confined, read-only bump workspace discovery.

Every rejection test places a valid member beside the invalid one and asserts
that Cargo was not invoked when the rejection is lexically decidable, so the
pre-Cargo contract is exercised rather than assumed.
"""

import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import (
    BumpPreflightError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from lading.workspace.confined_discovery import load_bump_workspace
from tests.unit.confined_discovery_helpers import (
    RecordingRunner as _RecordingRunner,
)
from tests.unit.confined_discovery_helpers import (
    WorkspaceShape,
)
from tests.unit.confined_discovery_helpers import (
    load_workspace as _load,
)
from tests.unit.confined_discovery_helpers import (
    payload as _payload,
)
from tests.unit.confined_discovery_helpers import (
    prepare_workspace as _prepare_workspace,
)
from tests.unit.confined_discovery_helpers import (
    write_manifest as _write_manifest,
)

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner


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
        options=WorkspaceShape(excludes=("crates/beta",)),
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
        options=WorkspaceShape(excludes=("crates/retired",)),
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
    # reports it without the workspace manifest declaring it.
    (tmp_path / "crates" / "alpha" / "Cargo.toml").write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n\n'
        '[dependencies]\nimplicit = { path = "../implicit" }\n',
        encoding="utf-8",
    )
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


def test_load_bump_workspace_accepts_an_explicit_root_member(tmp_path: Path) -> None:
    """A workspace that lists "." selects the root manifest and continues."""
    root = tmp_path / "workspace"
    (root / "crates" / "alpha").mkdir(parents=True)
    root_manifest = root / "Cargo.toml"
    root_manifest.write_text(
        '[package]\nname = "root"\nversion = "0.1.0"\n\n'
        '[workspace]\nmembers = [".", "crates/alpha"]\n',
        encoding="utf-8",
    )
    alpha_manifest = root / "crates" / "alpha" / "Cargo.toml"
    alpha_manifest.write_text(
        '[package]\nname = "alpha"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    payload = {
        "workspace_root": str(root),
        "packages": [
            {
                "id": "root-id",
                "name": "root",
                "version": "0.1.0",
                "manifest_path": str(root_manifest),
                "dependencies": [],
                "publish": None,
            },
            {
                "id": "alpha-id",
                "name": "alpha",
                "version": "0.1.0",
                "manifest_path": str(alpha_manifest),
                "dependencies": [],
                "publish": None,
            },
        ],
        "workspace_members": ["root-id", "alpha-id"],
    }

    graph, _runner = _load(root, payload)

    assert {crate.name for crate in graph.crates} == {"root", "alpha"}, (
        "an explicit root member must not prevent discovery of the other members"
    )
