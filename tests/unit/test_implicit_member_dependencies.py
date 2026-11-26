"""Dependency-shape tests for implicit workspace-member discovery.

Cargo reads path dependencies from several table spellings and only follows
``[workspace.dependencies]`` entries a package inherits. These tests pin the
shapes the preflight follows and the shapes it must ignore.
"""

import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import BumpPreflightError, UnsafeBumpTargetError
from lading.workspace.confined_discovery import load_bump_workspace
from tests.unit.implicit_member_helpers import (
    load,
    make_fifo,
    payload,
    runner_for,
    write_package,
    write_workspace,
)

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner


def _app_manifest(root: Path, text: str) -> None:
    """Write the app manifest with ``text`` as its dependency section."""
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n' + text,
        encoding="utf-8",
    )


def _reject(root: Path) -> None:
    """Assert that discovery rejects the workspace before Cargo runs."""
    runner = runner_for(payload(root, ("app", "dep")))
    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))
    assert "regular file" in str(excinfo.value), (
        "the diagnostic must identify the unsafe implicit manifest"
    )
    assert not runner.metadata_calls(), (
        "the implicit manifest must be rejected before Cargo runs"
    )


def test_an_unused_workspace_dependency_is_not_followed(tmp_path: Path) -> None:
    """A ``[workspace.dependencies]`` entry no package inherits is not opened."""
    root = tmp_path.resolve()
    write_workspace(root)
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n\n'
        "[workspace.dependencies]\n"
        'dep = { path = "vendor/local" }\n',
        encoding="utf-8",
    )
    (root / "vendor" / "local").mkdir(parents=True)

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "an unused workspace dependency must not fail the load"
    )


@pytest.mark.parametrize(
    "section",
    [
        pytest.param("[dev_dependencies]", id="package-dev"),
        pytest.param("[build_dependencies]", id="package-build"),
        pytest.param("[target.'cfg(unix)'.dev_dependencies]", id="target-dev"),
        pytest.param("[target.'cfg(unix)'.build_dependencies]", id="target-build"),
    ],
)
def test_a_legacy_dependency_table_is_validated(tmp_path: Path, section: str) -> None:
    """A pre-2024 dependency-table spelling is still followed."""
    root = tmp_path.resolve()
    write_workspace(root)
    _app_manifest(root, f'{section}\ndep = {{ path = "../dep" }}\n')
    make_fifo(root / "dep" / "Cargo.toml")

    _reject(root)


def test_a_normalised_dependency_path_is_validated(tmp_path: Path) -> None:
    """Cargo removes ``missing/..`` before lookup, so the target is checked."""
    root = tmp_path.resolve()
    write_workspace(root, dependency="../missing/../dep")
    make_fifo(root / "dep" / "Cargo.toml")

    _reject(root)


def test_non_table_root_sections_are_ignored(tmp_path: Path) -> None:
    """Root-level non-table values are not dependency tables."""
    root = tmp_path.resolve()
    write_workspace(root)
    (root / "app" / "Cargo.toml").write_text(
        "dependencies = 1\ntarget = 1\nworkspace = 1\n\n"
        '[package]\nname = "app"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "root-level non-table sections must not be inspected"
    )


def test_a_registry_dependency_is_ignored(tmp_path: Path) -> None:
    """A registry specification has no path to follow."""
    root = tmp_path.resolve()
    write_workspace(root)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        '[dependencies.registry]\nversion = "1.0"\n',
        encoding="utf-8",
    )

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a registry dependency must not be inspected as a path dependency"
    )


def test_a_version_only_dependency_is_ignored(tmp_path: Path) -> None:
    """A string dependency specification has no path."""
    root = tmp_path.resolve()
    write_workspace(root)
    _app_manifest(root, '[dependencies]\ndep = "1.0"\n')
    write_package(root / "dep", "dep")

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a version-only dependency must not be inspected as a path dependency"
    )


def test_two_level_workspace_inheritance_is_resolved(tmp_path: Path) -> None:
    """An implicit member that inherits another workspace dependency is followed."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n\n'
        "[workspace.dependencies]\n"
        'a = { path = "a" }\n'
        'b = { path = "b" }\n',
        encoding="utf-8",
    )
    (root / "app").mkdir(parents=True)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        "[dependencies]\na = { workspace = true }\n",
        encoding="utf-8",
    )
    (root / "a").mkdir()
    (root / "a" / "Cargo.toml").write_text(
        '[package]\nname = "a"\nversion = "0.1.0"\n\n'
        "[dependencies]\nb = { workspace = true }\n",
        encoding="utf-8",
    )
    make_fifo(root / "b" / "Cargo.toml")
    runner = runner_for(payload(root, ("app", "a", "b")))

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "regular file" in str(excinfo.value), (
        "the second-level inherited dependency must be inspected"
    )
    assert not runner.metadata_calls(), (
        "the second-level dependency must be rejected before Cargo runs"
    )


def test_a_member_workspace_pointer_to_another_root_is_rejected(
    tmp_path: Path,
) -> None:
    """A member that points at another workspace root is rejected before Cargo."""
    root = tmp_path.resolve()
    write_workspace(root)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion.workspace = true\n\nworkspace = "../trap"\n',
        encoding="utf-8",
    )
    trap = root / "trap"
    trap.mkdir()
    make_fifo(trap / "Cargo.toml")
    runner = runner_for(payload(root, ("app",)))

    with pytest.raises(BumpPreflightError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert str(trap) in str(excinfo.value), (
        "the diagnostic must name the pointer's target root"
    )
    assert not runner.metadata_calls(), (
        "the member pointer must be rejected before Cargo runs"
    )


def test_a_member_workspace_pointer_to_the_root_is_accepted(
    tmp_path: Path,
) -> None:
    """A member that correctly names the workspace root is accepted."""
    root = tmp_path.resolve()
    write_workspace(root)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\nworkspace = ".."\n',
        encoding="utf-8",
    )

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a correct member pointer must not reject the workspace"
    )
