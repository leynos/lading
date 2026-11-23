"""Unit tests for pre-Cargo validation of implicit workspace members.

Cargo reads the manifests of path dependencies that lie inside the workspace
while it discovers the member set. These tests prove that discovery rejects an
unreadable implicit manifest before Cargo runs, and that ordinary and
out-of-workspace path dependencies still load.
"""

import collections.abc as cabc
import json
import os
import typing as typ
from pathlib import Path

import pytest

from lading.utils.confinement import (
    BumpPreflightError,
    BumpTargetExaminationError,
    MissingBumpTargetError,
    UnsafeBumpTargetError,
)
from lading.workspace.confined_discovery import load_bump_workspace
from tests.helpers.recording_runner import RecordingCommandRunner

if typ.TYPE_CHECKING:
    from lading.runtime import CommandRunner


def _write_workspace(root: Path, *, dependency: str = "../dep") -> None:
    """Write a root workspace and an app that path-depends on ``dependency``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n', encoding="utf-8"
    )
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        f"[dependencies]\ndep = {{ path = {json.dumps(dependency)} }}\n",
        encoding="utf-8",
    )


def _write_package(directory: Path, name: str) -> Path:
    """Write one package manifest and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return manifest


def _payload(root: Path, names: cabc.Sequence[str]) -> dict[str, typ.Any]:
    """Return a metadata payload for packages under ``root``."""
    packages = [
        {
            "id": f"{name}-id",
            "name": name,
            "version": "0.1.0",
            "manifest_path": str(root / name / "Cargo.toml"),
            "dependencies": [],
            "publish": None,
        }
        for name in names
    ]
    return {
        "workspace_root": str(root),
        "packages": packages,
        "workspace_members": [f"{name}-id" for name in names],
    }


def _runner(payload: dict[str, typ.Any], root: Path) -> RecordingCommandRunner:
    """Return a recording runner serving ``payload``."""
    return RecordingCommandRunner(metadata=payload)


def _make_fifo(path: Path) -> None:
    """Create a FIFO at ``path``, skipping when the host cannot."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkfifo(path)
    except OSError as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")


def _symlinked_member(tmp_path: Path) -> None:
    """Replace ``dep`` with a symlink to an external package."""
    root = tmp_path.resolve() / "workspace"
    outside = tmp_path.resolve() / "outside"
    _write_package(outside, "dep")
    (root / "dep").symlink_to(outside, target_is_directory=True)


def _absolute_dependency(root: Path) -> None:
    """Point the dependency at an absolute in-workspace path."""
    _write_workspace(root, dependency=str(root / "dep"))


def _target_table_dependency(root: Path) -> None:
    """Declare the dependency under a target table."""
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n', encoding="utf-8"
    )
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        "[target.'cfg(unix)'.dependencies]\n"
        'dep = { path = "../dep" }\n',
        encoding="utf-8",
    )


def _workspace_dependency(root: Path) -> None:
    """Declare the dependency in ``[workspace.dependencies]``."""
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n\n'
        "[workspace.dependencies]\n"
        'dep = { path = "dep" }\n',
        encoding="utf-8",
    )
    (root / "app").mkdir(exist_ok=True)
    _write_package(root / "app", "app")


@pytest.mark.parametrize(
    ("prepare", "detail"),
    [
        pytest.param(
            lambda _root, _tmp: None,
            "regular file",
            id="declared-dependency-fifo",
        ),
        pytest.param(
            lambda _root, tmp: _symlinked_member(tmp),
            "symlink",
            id="symlinked-dependency",
        ),
        pytest.param(
            lambda root, _tmp: _absolute_dependency(root),
            "regular file",
            id="absolute-in-workspace",
        ),
        pytest.param(
            lambda root, _tmp: _target_table_dependency(root),
            "regular file",
            id="target-table",
        ),
        pytest.param(
            lambda root, _tmp: _workspace_dependency(root),
            "regular file",
            id="workspace-dependencies",
        ),
    ],
)
def test_an_unsafe_implicit_manifest_is_rejected_before_cargo(
    tmp_path: Path,
    prepare: cabc.Callable[[Path, Path], None],
    detail: str,
) -> None:
    """An unsafe implicit member is rejected before Cargo reads it."""
    root = tmp_path.resolve() / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    _write_workspace(root)
    prepare(root, tmp_path)
    if detail == "regular file":
        _make_fifo(root / "dep" / "Cargo.toml")
    runner = _runner(_payload(root, ("app", "dep")), root)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert detail in str(excinfo.value), (
        "the diagnostic must identify the unsafe implicit manifest"
    )
    assert not runner.metadata_calls(), (
        "the implicit manifest must be rejected before Cargo runs"
    )


def test_a_regular_implicit_member_is_accepted(tmp_path: Path) -> None:
    """A regular implicit member manifest lets discovery proceed."""
    root = tmp_path.resolve()
    _write_workspace(root)
    _write_package(root / "dep", "dep")
    runner = _runner(_payload(root, ("app", "dep")), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app", "dep"}, (
        "the implicit member must appear in the discovered graph"
    )
    assert runner.metadata_calls(), "a valid implicit member must reach Cargo"


def test_a_path_dependency_outside_the_workspace_is_left_to_cargo(
    tmp_path: Path,
) -> None:
    """A dependency outside the workspace is not an implicit member."""
    root = tmp_path.resolve() / "workspace"
    _write_workspace(root, dependency="../../outside/dep")
    _write_package(tmp_path.resolve() / "outside" / "dep", "dep")
    runner = _runner(_payload(root, ("app",)), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "an out-of-workspace dependency must not appear as a member"
    )
    assert runner.metadata_calls(), "discovery must still reach Cargo"


def test_a_nested_implicit_member_is_validated(tmp_path: Path) -> None:
    """An implicit member's own path dependency is inspected too."""
    root = tmp_path.resolve()
    _write_workspace(root)
    (root / "dep").mkdir()
    (root / "dep" / "Cargo.toml").write_text(
        '[package]\nname = "dep"\nversion = "0.1.0"\n\n'
        '[dependencies]\nnested = { path = "../nested" }\n',
        encoding="utf-8",
    )
    _make_fifo(root / "nested" / "Cargo.toml")
    runner = _runner(_payload(root, ("app", "dep", "nested")), root)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "regular file" in str(excinfo.value), (
        "a nested implicit member must be inspected"
    )


def test_an_absolute_dependency_outside_the_workspace_is_skipped(
    tmp_path: Path,
) -> None:
    """An absolute path dependency outside the workspace is left to Cargo."""
    root = tmp_path.resolve() / "workspace"
    outside = tmp_path.resolve() / "outside" / "dep"
    _write_workspace(root, dependency=str(outside))
    _write_package(outside, "dep")
    runner = _runner(_payload(root, ("app",)), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "an out-of-workspace absolute dependency must not be a member"
    )


def test_a_dependency_on_a_regular_file_is_skipped(tmp_path: Path) -> None:
    """A path dependency naming a file is not an implicit member."""
    root = tmp_path.resolve()
    _write_workspace(root, dependency="../file.txt")
    (root / "file.txt").write_text("content", encoding="utf-8")
    runner = _runner(_payload(root, ("app",)), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a file path dependency must not be inspected as a manifest"
    )


def test_a_missing_dependency_directory_is_skipped(tmp_path: Path) -> None:
    """A path dependency that does not exist is left for Cargo to report."""
    root = tmp_path.resolve()
    _write_workspace(root, dependency="../missing")
    runner = _runner(_payload(root, ("app",)), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a missing dependency directory must not be treated as a member"
    )


def _missing_manifest(root: Path) -> None:
    """Leave the dependency directory without a manifest."""


def _invalid_manifest(root: Path) -> None:
    """Write a dependency manifest that is not valid TOML."""
    (root / "dep" / "Cargo.toml").write_text("not toml [\n", encoding="utf-8")


def _undecodable_manifest(root: Path) -> None:
    """Write a dependency manifest that is not valid UTF-8."""
    (root / "dep" / "Cargo.toml").write_bytes(b"\xff\xfe")


@pytest.mark.parametrize(
    ("prepare", "error", "expected"),
    [
        pytest.param(
            _missing_manifest,
            MissingBumpTargetError,
            "dep/Cargo.toml",
            id="missing",
        ),
        pytest.param(
            _invalid_manifest,
            BumpPreflightError,
            "not valid TOML",
            id="invalid-toml",
        ),
        pytest.param(
            _undecodable_manifest,
            BumpTargetExaminationError,
            "cannot read",
            id="undecodable",
        ),
    ],
)
def test_a_broken_dependency_manifest_is_rejected(
    tmp_path: Path,
    prepare: cabc.Callable[[Path], None],
    error: type[Exception],
    expected: str,
) -> None:
    """A dependency manifest that cannot be used fails before Cargo."""
    root = tmp_path.resolve()
    _write_workspace(root)
    (root / "dep").mkdir()
    prepare(root)
    runner = _runner(_payload(root, ("app",)), root)

    with pytest.raises(error) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert expected in str(excinfo.value), (
        "the diagnostic must explain the broken dependency manifest"
    )


def test_an_unreadable_dependency_component_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A component that cannot be examined is an examination error."""
    root = tmp_path.resolve()
    _write_workspace(root)
    (root / "dep").mkdir()
    target = root / "dep"
    real_lstat = os.lstat

    def failing_lstat(path: str | os.PathLike[str]) -> os.stat_result:
        """Fail only for the dependency directory."""
        if os.fspath(path) == str(target):
            message = "denied"
            raise PermissionError(13, message, str(path))
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", failing_lstat)
    runner = _runner(_payload(root, ("app",)), root)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "cannot examine path dependency" in str(excinfo.value), (
        "the diagnostic must name the unreadable dependency component"
    )


def test_non_table_dependency_shapes_are_ignored(tmp_path: Path) -> None:
    """Registry dependencies and non-table sections have no path to follow."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n', encoding="utf-8"
    )
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        "dependencies = 1\ntarget = 1\nworkspace = 1\n\n"
        '[dependencies.registry]\nversion = "1.0"\n',
        encoding="utf-8",
    )
    runner = _runner(_payload(root, ("app",)), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a registry dependency must not be inspected as a path dependency"
    )


def test_an_undecodable_dependency_manifest_is_rejected(tmp_path: Path) -> None:
    """A dependency manifest that is not valid UTF-8 is an examination error."""
    root = tmp_path.resolve()
    _write_workspace(root)
    (root / "dep").mkdir()
    (root / "dep" / "Cargo.toml").write_bytes(b"\xff\xfe")
    runner = _runner(_payload(root, ("app",)), root)

    with pytest.raises(BumpTargetExaminationError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "cannot read" in str(excinfo.value), (
        "the diagnostic must name the undecodable dependency manifest"
    )


def test_a_dependency_table_value_that_is_a_string_is_ignored(
    tmp_path: Path,
) -> None:
    """A version-only dependency has no path to follow."""
    root = tmp_path.resolve()
    _write_workspace(root)
    (root / "dep").mkdir()
    _write_package(root / "dep", "dep")
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n[dependencies]\ndep = "1.0"\n',
        encoding="utf-8",
    )
    runner = _runner(_payload(root, ("app",)), root)

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a version-only dependency must not be inspected as a path dependency"
    )


def test_a_dot_spelling_resolves_to_the_same_implicit_member(
    tmp_path: Path,
) -> None:
    """A ``./dep`` spelling normalises to the same dependency directory."""
    root = tmp_path.resolve()
    _write_workspace(root, dependency="./.././dep")
    _make_fifo(root / "dep" / "Cargo.toml")
    runner = _runner(_payload(root, ("app", "dep")), root)

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "regular file" in str(excinfo.value), (
        "a dot component must not hide the dependency"
    )
