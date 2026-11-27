"""Filesystem rejection tests for implicit workspace-member discovery.

Cargo reads the manifests of path dependencies inside the workspace while it
discovers the member set. These tests prove that an unreadable, symlinked, or
malformed implicit manifest is rejected before Cargo runs, and that ordinary
path dependencies still load.
"""

import collections.abc as cabc
import errno
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


def _symlinked_member(tmp_path: Path) -> None:
    """Replace ``dep`` with a symlink to an external package."""
    root = tmp_path.resolve() / "workspace"
    outside = tmp_path.resolve() / "outside"
    write_package(outside, "dep")
    _symlink_or_skip(root / "dep", outside)


def _absolute_dependency(root: Path) -> None:
    """Point the dependency at an absolute in-workspace path."""
    write_workspace(root, dependency=str(root / "dep"))


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
    """Declare the dependency in ``[workspace.dependencies]`` and inherit it."""
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\n\n'
        "[workspace.dependencies]\n"
        'dep = { path = "dep" }\n',
        encoding="utf-8",
    )
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        "[dependencies]\ndep = { workspace = true }\n",
        encoding="utf-8",
    )


def _symlink_or_skip(path: Path, target: Path) -> None:
    """Create a directory symlink, skipping only an unsupported or denied host."""
    try:
        path.symlink_to(target, target_is_directory=True)
    except NotImplementedError:
        pytest.skip("directory symlinks are unavailable on this host")
    except OSError as exc:
        if exc.errno not in {errno.EPERM, errno.EACCES}:
            raise
        pytest.skip(f"symlink creation is unavailable here: {exc}")


@pytest.mark.parametrize(
    ("prepare", "detail", "fixture"),
    [
        pytest.param(lambda _root, _tmp: None, "regular file", "fifo", id="fifo"),
        pytest.param(
            lambda _root, tmp: _symlinked_member(tmp),
            "symlink",
            "prepared",
            id="symlinked-dependency",
        ),
        pytest.param(
            lambda root, _tmp: _absolute_dependency(root),
            "regular file",
            "directory",
            id="absolute-in-workspace",
        ),
        pytest.param(
            lambda root, _tmp: _target_table_dependency(root),
            "regular file",
            "directory",
            id="target-table",
        ),
        pytest.param(
            lambda root, _tmp: _workspace_dependency(root),
            "regular file",
            "directory",
            id="workspace-dependencies",
        ),
    ],
)
def test_an_unsafe_implicit_manifest_is_rejected_before_cargo(
    tmp_path: Path,
    prepare: cabc.Callable[[Path, Path], None],
    detail: str,
    *,
    fixture: str,
) -> None:
    """An unsafe implicit member is rejected before Cargo reads it.

    The absolute-path, target-table and workspace-inheritance cases need only
    a non-regular manifest, so they use a directory named ``Cargo.toml`` and
    run even where ``mkfifo`` is unavailable. The FIFO case selects the FIFO
    shape explicitly.
    """
    root = tmp_path.resolve() / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    write_workspace(root)
    prepare(root, tmp_path)
    manifest = root / "dep" / "Cargo.toml"
    match fixture:
        case "fifo":
            manifest.parent.mkdir(parents=True, exist_ok=True)
            make_fifo(manifest)
        case "directory":
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.mkdir()
    runner = runner_for(payload(root, ("app", "dep")))

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
    write_workspace(root)
    write_package(root / "dep", "dep")
    runner = runner_for(payload(root, ("app", "dep")))

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
    write_workspace(root, dependency="../../outside/dep")
    write_package(tmp_path.resolve() / "outside" / "dep", "dep")

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "an out-of-workspace dependency must not appear as a member"
    )


def test_a_nested_implicit_member_is_validated(tmp_path: Path) -> None:
    """An implicit member's own path dependency is inspected too."""
    root = tmp_path.resolve()
    write_workspace(root)
    (root / "dep").mkdir()
    (root / "dep" / "Cargo.toml").write_text(
        '[package]\nname = "dep"\nversion = "0.1.0"\n\n'
        '[dependencies]\nnested = { path = "../nested" }\n',
        encoding="utf-8",
    )
    make_fifo(root / "nested" / "Cargo.toml")
    runner = runner_for(payload(root, ("app", "dep", "nested")))

    with pytest.raises(UnsafeBumpTargetError) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert "regular file" in str(excinfo.value), (
        "a nested implicit member must be inspected"
    )
    assert not runner.metadata_calls(), (
        "the nested member must be rejected before Cargo runs"
    )


def test_an_absolute_dependency_outside_the_workspace_is_skipped(
    tmp_path: Path,
) -> None:
    """An absolute path dependency outside the workspace is left to Cargo."""
    root = tmp_path.resolve() / "workspace"
    outside = tmp_path.resolve() / "outside" / "dep"
    write_workspace(root, dependency=str(outside))
    write_package(outside, "dep")

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "an out-of-workspace absolute dependency must not be a member"
    )


def test_a_dependency_on_a_regular_file_is_skipped(tmp_path: Path) -> None:
    """A path dependency naming a file is not an implicit member."""
    root = tmp_path.resolve()
    write_workspace(root, dependency="../file.txt")
    (root / "file.txt").write_text("content", encoding="utf-8")

    graph = load(root, payload(root, ("app",)))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "a file path dependency must not be inspected as a manifest"
    )


def test_a_missing_dependency_directory_is_skipped(tmp_path: Path) -> None:
    """A path dependency that does not exist is left for Cargo to report."""
    root = tmp_path.resolve()
    write_workspace(root, dependency="../missing")

    graph = load(root, payload(root, ("app",)))

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
    write_workspace(root)
    (root / "dep").mkdir()
    prepare(root)
    runner = runner_for(payload(root, ("app",)))

    with pytest.raises(error) as excinfo:
        load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert expected in str(excinfo.value), (
        "the diagnostic must explain the broken dependency manifest"
    )
    assert not runner.metadata_calls(), (
        "the broken manifest must be rejected before Cargo runs"
    )


def test_an_excluded_nested_workspace_is_not_read(tmp_path: Path) -> None:
    """An excluded nested workspace is not a member and is left unread."""
    root = tmp_path.resolve()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\nexclude = ["vendor/tool"]\n',
        encoding="utf-8",
    )
    (root / "app").mkdir()
    (root / "app" / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        '[dependencies]\ntool = { path = "../vendor/tool" }\n',
        encoding="utf-8",
    )
    nested = root / "vendor" / "tool"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text(
        '[package]\nname = "tool"\nversion = "0.1.0"\n\nworkspace = "../.."\n',
        encoding="utf-8",
    )
    runner = runner_for(payload(root, ("app",)))

    graph = load_bump_workspace(root, runner=typ.cast("CommandRunner", runner))

    assert {crate.name for crate in graph.crates} == {"app"}, (
        "the excluded nested workspace must not enter the member set"
    )
