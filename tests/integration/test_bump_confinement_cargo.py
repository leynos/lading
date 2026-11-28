"""Integration tests for confined bump discovery against real Cargo.

These tests run the installed ``cargo`` against small path-only workspaces with
an isolated ``CARGO_HOME`` and offline networking. They establish the read-only
discovery contract (a missing or stale lockfile is untouched by
``cargo metadata --no-deps``), the dry-run contract, and the effective
workspace-root lockfile destinations that a stub cannot prove.
"""

import collections.abc as cabc
import json
import shutil
import typing as typ
from pathlib import Path

import pytest

from lading.commands import bump
from lading.commands.bump_lockfile_planning import (
    LockfilePlan,
    plan_lockfiles,
)
from lading.commands.bump_lockfile_regeneration import regenerate_planned
from lading.utils.confinement import (
    BumpPreflightError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from lading.workspace import load_cargo_metadata
from lading.workspace.confined_discovery import load_bump_workspace
from lading.workspace.metadata import MetadataQuery
from tests.helpers.fs_snapshot import changed_paths, snapshot_tree
from tests.helpers.workspace_builders import _make_config

pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        shutil.which("cargo") is None, reason="cargo is unavailable on this host"
    ),
]

_CARGO_TOML = "Cargo.toml"


@pytest.fixture(autouse=True)
def _isolated_cargo(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Isolate Cargo state and refuse network access for every test."""
    monkeypatch.setenv("CARGO_HOME", str(tmp_path_factory.mktemp("cargo-home")))
    monkeypatch.setenv("CARGO_NET_OFFLINE", "true")
    monkeypatch.delenv("CARGO_RESOLVER_LOCKFILE_PATH", raising=False)


def _write_package(
    directory: Path,
    name: str,
    *,
    dependencies: str = "",
    standalone: bool = False,
) -> Path:
    """Write one package and return its manifest path."""
    (directory / "src").mkdir(parents=True)
    (directory / "src" / "lib.rs").write_text("", encoding="utf-8")
    workspace_table = "\n[workspace]\n" if standalone else ""
    manifest = directory / _CARGO_TOML
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'
        f"{dependencies}{workspace_table}",
        encoding="utf-8",
    )
    return manifest


def _write_workspace(
    root: Path,
    *,
    members: cabc.Sequence[str] = ("crates/core", "crates/app"),
) -> None:
    """Write a virtual workspace with core and an app that path-depends on it."""
    (root / "crates" / "core").mkdir(parents=True, exist_ok=True)
    _write_package(root / "crates" / "core", "core")
    (root / "crates" / "app").mkdir(parents=True, exist_ok=True)
    _write_package(
        root / "crates" / "app",
        "app",
        dependencies=(
            '\n[dependencies]\ncore = { path = "../core", version = "0.1.0" }\n'
        ),
    )
    members_literal = ", ".join(f'"{member}"' for member in members)
    (root / _CARGO_TOML).write_text(
        f'[workspace]\nmembers = [{members_literal}]\nresolver = "2"\n\n'
        '[workspace.package]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def _write_nested_workspace(root: Path) -> Path:
    """Write a standalone nested workspace and return its manifest."""
    return _write_package(root / "fixtures" / "nested", "nested", standalone=True)


def _stale_lockfile(path: Path) -> None:
    """Write a syntactically valid but stale lockfile."""
    path.write_text(
        'version = 3\n\n[[package]]\nname = "stale"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )


def test_discovery_leaves_a_missing_lockfile_absent(tmp_path: Path) -> None:
    """``cargo metadata --no-deps`` creates no lockfile."""
    root = tmp_path.resolve()
    _write_workspace(root)
    before = snapshot_tree(root)

    graph = load_bump_workspace(root)

    assert {crate.name for crate in graph.crates} == {"core", "app"}, (
        "discovery must report both workspace members"
    )
    assert not changed_paths(before, snapshot_tree(root)), (
        "discovery must not create or modify any file"
    )
    assert not (root / "Cargo.lock").exists(), (
        "no-deps discovery must not create a lockfile"
    )


def test_discovery_leaves_a_stale_lockfile_untouched(tmp_path: Path) -> None:
    """A stale lockfile is not refreshed by discovery."""
    root = tmp_path.resolve()
    _write_workspace(root)
    _stale_lockfile(root / "Cargo.lock")
    before = snapshot_tree(root)

    load_bump_workspace(root)

    assert not changed_paths(before, snapshot_tree(root)), (
        "discovery must not rewrite a stale lockfile"
    )


def test_dry_run_reports_the_lockfile_without_writing(tmp_path: Path) -> None:
    """A dry run names the root lockfile and leaves the workspace unchanged."""
    root = tmp_path.resolve()
    _write_workspace(root)
    workspace = load_bump_workspace(root)
    before = snapshot_tree(root)

    message = bump.run(
        root,
        "1.2.3",
        options=bump.BumpOptions(
            dry_run=True,
            rebuild_lockfiles=True,
            configuration=_make_config(),
            workspace=workspace,
        ),
    )

    assert "- Cargo.lock (lockfile)" in message, (
        "the dry run must name the planned root lockfile"
    )
    assert not changed_paths(before, snapshot_tree(root)), (
        "a dry run must not modify the workspace"
    )
    assert not (root / "Cargo.lock").exists(), "a dry run must not create the lockfile"


def test_live_bump_creates_the_root_lockfile(tmp_path: Path) -> None:
    """A live bump rewrites manifests and creates the planned lockfile."""
    root = tmp_path.resolve()
    _write_workspace(root)
    workspace = load_bump_workspace(root)

    message = bump.run(
        root,
        "1.2.3",
        options=bump.BumpOptions(
            rebuild_lockfiles=True,
            configuration=_make_config(),
            workspace=workspace,
        ),
    )

    assert "- Cargo.lock (lockfile)" in message, (
        "the live summary must name the regenerated root lockfile"
    )
    assert (root / "Cargo.lock").exists(), "the live run must create the root lockfile"
    assert 'version = "1.2.3"' in (root / "crates" / "app" / _CARGO_TOML).read_text(
        encoding="utf-8"
    ), "the member manifest must carry the new version"


def test_metadata_fields_agree_between_discovery_modes(tmp_path: Path) -> None:
    """``--no-deps`` changes the dependency detail, not the member set."""
    root = tmp_path.resolve()
    _write_workspace(root)

    full = load_cargo_metadata(root)
    no_deps = load_cargo_metadata(root, query=MetadataQuery(no_deps=True))

    assert full["workspace_root"] == no_deps["workspace_root"], (
        "both modes must report the same workspace root"
    )
    assert set(full["workspace_members"]) == set(no_deps["workspace_members"]), (
        "both modes must report the same member identifiers"
    )

    def _member_manifests(payload: cabc.Mapping[str, typ.Any]) -> set[tuple[str, str]]:
        """Return (name, manifest_path) for each workspace member package."""
        members = set(payload["workspace_members"])
        return {
            (package["name"], package["manifest_path"])
            for package in payload["packages"]
            if package["id"] in members
        }

    assert _member_manifests(full) == _member_manifests(no_deps), (
        "both modes must agree on the member manifests"
    )


def test_member_patterns_match_valid_workspaces(tmp_path: Path) -> None:
    """A member glob selects real directories and ignores stray files."""
    root = tmp_path.resolve()
    _write_workspace(root, members=("crates/*",))
    (root / "crates" / "README.md").write_text("stray\n", encoding="utf-8")

    graph = load_bump_workspace(root)

    assert {crate.name for crate in graph.crates} == {"core", "app"}, (
        "a stray file under crates/ must not become a member"
    )


@pytest.mark.parametrize(
    "members",
    [
        pytest.param(("crates/missing",), id="missing-literal"),
        pytest.param(("missing/*",), id="unmatched-glob"),
    ],
)
def test_member_pattern_outcomes_are_reported(
    tmp_path: Path, members: cabc.Sequence[str]
) -> None:
    """A missing literal and an unmatched glob both fail with the pattern."""
    root = tmp_path.resolve()
    _write_workspace(root, members=members)

    with pytest.raises(MissingBumpTargetError) as excinfo:
        load_bump_workspace(root)

    assert members[0] in str(excinfo.value), (
        "the diagnostic must name the rejected member pattern"
    )


def test_nested_standalone_manifest_keeps_its_own_lockfile(tmp_path: Path) -> None:
    """Cargo's effective root yields distinct root and nested lockfiles."""
    root = tmp_path.resolve()
    _write_workspace(root)
    nested = _write_nested_workspace(root)
    configured = ("crates/app/Cargo.toml", "fixtures/nested/Cargo.toml")

    plan = plan_lockfiles(root, configured, environ={})

    assert plan.lockfiles == (root / "Cargo.lock", nested.parent / "Cargo.lock"), (
        "a member manifest must dedupe to the root lockfile while the standalone "
        "nested workspace keeps its own"
    )
    assert plan.manifests == (root / _CARGO_TOML, nested), (
        "regeneration must run once per effective workspace root"
    )


def test_regeneration_creates_every_planned_lockfile(tmp_path: Path) -> None:
    """Regenerating a validated plan creates each planned destination."""
    root = tmp_path.resolve()
    _write_workspace(root)
    nested = _write_nested_workspace(root)
    plan = LockfilePlan(
        root=root,
        manifests=(root / _CARGO_TOML, nested),
        lockfiles=(root / "Cargo.lock", nested.parent / "Cargo.lock"),
    )

    lockfiles = regenerate_planned(plan)

    assert lockfiles == plan.lockfiles, (
        "regeneration must report the validated destinations"
    )
    for lockfile in lockfiles:
        assert lockfile.exists(), f"the planned lockfile must exist: {lockfile}"


def test_symlinked_member_is_rejected_without_touching_the_target(
    tmp_path: Path,
) -> None:
    """A member directory symlink is rejected before Cargo runs."""
    root = tmp_path.resolve() / "workspace"
    outside = tmp_path.resolve() / "outside"
    _write_workspace(root)
    shutil.rmtree(root / "crates" / "app")
    _write_package(outside, "outside")
    (root / "crates" / "app").symlink_to(outside, target_is_directory=True)
    before_outside = snapshot_tree(outside)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root)

    assert "symlink" in str(excinfo.value), (
        "the diagnostic must identify the symlinked member"
    )
    assert not changed_paths(before_outside, snapshot_tree(outside)), (
        "the external target must be untouched"
    )


def test_workspace_reached_through_a_symlinked_ancestor_is_valid(
    tmp_path: Path,
) -> None:
    """A symlink above the workspace root does not invalidate the workspace."""
    real_parent = tmp_path.resolve() / "real"
    root = real_parent / "workspace"
    _write_workspace(root)
    link_parent = tmp_path.resolve() / "link"
    link_parent.symlink_to(real_parent, target_is_directory=True)

    graph = load_bump_workspace(link_parent / "workspace")

    assert graph.workspace_root == root, (
        "the graph root must resolve to the real workspace directory"
    )
    assert {crate.name for crate in graph.crates} == {"core", "app"}, (
        "the symlinked ancestor must not hide any member"
    )


def test_cargo_config_lockfile_override_is_rejected_before_writing(
    tmp_path: Path,
) -> None:
    """A resolver.lockfile-path in Cargo configuration cannot redirect a bump."""
    root = tmp_path.resolve() / "workspace"
    _write_workspace(root)
    outside = tmp_path.resolve() / "outside" / "Cargo.lock"
    config = root / ".cargo" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        f"[resolver]\nlockfile-path = {json.dumps(str(outside))}\n",
        encoding="utf-8",
    )
    workspace = load_bump_workspace(root)
    before = snapshot_tree(root)

    with pytest.raises(BumpPreflightError) as excinfo:
        bump.run(
            root,
            "1.2.3",
            options=bump.BumpOptions(
                rebuild_lockfiles=True,
                configuration=_make_config(),
                workspace=workspace,
            ),
        )

    assert str(config) in str(excinfo.value), (
        "the diagnostic must name the configuration file carrying the override"
    )
    assert not changed_paths(before, snapshot_tree(root)), (
        "a rejected lockfile plan must leave every manifest unchanged"
    )
    assert not outside.exists(), (
        "the external lockfile named by the override must not be written"
    )
    assert not (root / "Cargo.lock").exists(), (
        "no lockfile may be created before the override is rejected"
    )
