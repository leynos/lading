"""Unit tests for copying the workspace tree into staging.

``_copy_workspace_tree`` mirrors the workspace into the build directory. The
tests here cover the copy itself, replacement of a stale clone, symlink
handling, and the two refusals: a filesystem failure that must surface through
the staging error boundary, and a target nested inside the workspace.
"""

import typing as typ
from pathlib import Path

import pytest

from lading.commands import publish_staging


class _CopyWorkspaceFailureCase(typ.NamedTuple):
    """Failure mode exercised while copying a workspace tree."""

    operation: str
    requires_staging_root: bool
    message: str


def test_copy_workspace_tree_mirrors_workspace_contents(tmp_path: Path) -> None:
    """Workspace files are cloned into the staging directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest = workspace_root / "Cargo.toml"
    manifest.write_text("[workspace]\n", encoding="utf-8")
    nested_dir = workspace_root / "crates" / "alpha"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "README.md"
    nested_file.write_text("# README\n", encoding="utf-8")

    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=True
    )

    assert staging_root == build_directory / workspace_root.name, (
        "the staged copy must live in a directory named after the workspace"
    )
    assert (staging_root / "Cargo.toml").read_text(encoding="utf-8") == (
        "[workspace]\n"
    ), "top-level workspace files must be copied into the staging tree"
    assert (staging_root / "crates" / "alpha" / "README.md").read_text(
        encoding="utf-8"
    ) == "# README\n", "nested workspace files must be copied into the staging tree"


def test_copy_workspace_tree_replaces_existing_clone(tmp_path: Path) -> None:
    """Existing staging directories are replaced with a fresh copy."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "marker.txt").write_text("fresh", encoding="utf-8")

    build_directory = tmp_path / "staging"
    existing_clone = build_directory / workspace_root.name
    existing_clone.mkdir(parents=True)
    stale_file = existing_clone / "stale.txt"
    stale_file.write_text("stale", encoding="utf-8")

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=True
    )

    assert staging_root == existing_clone, (
        "a re-copy must reuse the same staging root as the existing clone"
    )
    assert not stale_file.exists(), (
        "files from a previous clone must not survive the fresh copy"
    )
    assert (staging_root / "marker.txt").read_text(encoding="utf-8") == "fresh", (
        "the fresh copy must carry the current workspace contents"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _CopyWorkspaceFailureCase(
                "rmtree", requires_staging_root=True, message="permission denied"
            ),
            id="staging_cleanup",
        ),
        pytest.param(
            _CopyWorkspaceFailureCase(
                "copytree", requires_staging_root=False, message="disk full"
            ),
            id="workspace_copy",
        ),
    ],
)
def test_copy_workspace_tree_wraps_filesystem_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _CopyWorkspaceFailureCase,
) -> None:
    """Workspace-copy filesystem failures use the staging error boundary."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    build_directory = tmp_path / "staging"
    if case.requires_staging_root:
        (build_directory / workspace_root.name).mkdir(parents=True)
    else:
        build_directory.mkdir()

    failure = OSError(case.message)

    def fail_operation(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(publish_staging.shutil, case.operation, fail_operation)

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._copy_workspace_tree(
            workspace_root, build_directory, preserve_symlinks=True
        )

    assert "Cannot copy workspace into staging directory" in str(excinfo.value), (
        f"a {case.operation} failure must be reported through the staging error "
        "boundary"
    )
    assert excinfo.value.__cause__ is failure, (
        f"the OSError raised by {case.operation} must be preserved as the cause"
    )


def test_copy_workspace_tree_rejects_nested_clone(tmp_path: Path) -> None:
    """Copying into a directory under the workspace is prohibited."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._copy_workspace_tree(
            workspace_root, workspace_root, preserve_symlinks=True
        )

    assert "cannot be nested inside the workspace root" in str(excinfo.value), (
        "copying the workspace into itself must be refused with the documented reason"
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            {"preserve_symlinks": True, "expect_symlink": True},
            id="preserve",
        ),
        pytest.param(
            {"preserve_symlinks": False, "expect_symlink": False},
            id="dereference",
        ),
    ],
)
def test_copy_workspace_tree_symlink_handling(
    tmp_path: Path, scenario: dict[str, bool]
) -> None:
    """Workspace symlinks are preserved or dereferenced based on option."""
    preserve_symlinks = scenario["preserve_symlinks"]
    expect_symlink = scenario["expect_symlink"]
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "data.txt"
    target.write_text("payload", encoding="utf-8")
    link = workspace_root / "alias.txt"
    link.symlink_to(target.name)

    build_directory = tmp_path / "staging"
    build_directory.mkdir()

    staging_root = publish_staging._copy_workspace_tree(
        workspace_root, build_directory, preserve_symlinks=preserve_symlinks
    )

    staged_link = staging_root / "alias.txt"
    assert staged_link.is_file(), (
        "the staged symlink must still resolve to a regular file"
    )
    assert staged_link.is_symlink() == expect_symlink, (
        "preserve_symlinks must decide whether the staged link stays a symlink"
    )
    if expect_symlink:
        assert staged_link.resolve(strict=True) == staging_root / "data.txt", (
            "a preserved symlink must still point at the staged data file"
        )
    assert staged_link.read_text(encoding="utf-8") == "payload", (
        "the staged link must expose the original file contents"
    )
