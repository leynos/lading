"""Unit tests for publish build-directory normalization.

The build directory is where staging places its workspace copy. These tests
cover the three inputs ``_normalize_build_directory`` accepts — none, a
relative path, and an explicit path — plus the two ways it must refuse or wrap
a bad target.
"""

import shutil
from pathlib import Path

import pytest

from lading.commands import publish_staging
from tests.helpers.cwd import chdir_for_test


def test_normalize_build_directory_defaults_to_tempdir(tmp_path: Path) -> None:
    """Normalization creates a temporary directory when none is provided.

    The directory is removed here because nothing else will: this helper is
    below the level that registers cleanup, and this test alone left 3,925
    empty directories on a shared host (issue #269).
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    build_directory = publish_staging._normalize_build_directory(workspace_root, None)
    try:
        assert build_directory.exists(), (
            "a default build directory must exist on disk after normalization"
        )
        assert build_directory.is_absolute(), (
            "the build directory must be resolved to an absolute path"
        )
        assert not build_directory.is_relative_to(workspace_root), (
            "an automatic build directory must not sit under the workspace root"
        )
    finally:
        shutil.rmtree(build_directory, ignore_errors=True)


def test_normalize_build_directory_resolves_relative_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative build directories are resolved against the current directory."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    chdir_for_test(monkeypatch, tmp_path)

    build_directory = publish_staging._normalize_build_directory(
        workspace_root, Path("staging")
    )

    expected = (tmp_path / "staging").resolve()
    assert build_directory == expected, (
        "a relative build directory must resolve against the current directory"
    )
    assert build_directory.exists(), (
        "normalization must create the resolved build directory"
    )


def test_normalize_build_directory_rejects_workspace_descendants(
    tmp_path: Path,
) -> None:
    """Normalization rejects build directories nested under the workspace."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    build_directory = workspace_root / "target"

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._normalize_build_directory(workspace_root, build_directory)

    assert "cannot reside within the workspace root" in str(excinfo.value), (
        "a build directory nested under the workspace must be rejected with the "
        "documented reason"
    )


def test_normalize_build_directory_wraps_creation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Build-directory creation failures use the staging error boundary."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def fail_mkdir(*_args: object, **_kwargs: object) -> None:
        message = "permission denied"
        raise OSError(message)

    monkeypatch.setattr(publish_staging.Path, "mkdir", fail_mkdir)

    with pytest.raises(publish_staging.PublishPreparationError) as excinfo:
        publish_staging._normalize_build_directory(workspace_root, tmp_path / "staging")

    assert "Cannot create publish build directory" in str(excinfo.value), (
        "a failed mkdir must surface through the staging error boundary"
    )
    assert isinstance(excinfo.value.__cause__, OSError), (
        "the original OSError must be preserved as the cause"
    )
