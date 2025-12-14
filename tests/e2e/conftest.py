"""Pytest fixtures for end-to-end lading CLI tests."""

from __future__ import annotations

import typing as typ

import pytest

from tests.e2e.helpers import git_helpers, workspace_builder

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path
else:  # pragma: no cover - runtime typing fallback
    Path = typ.Any  # type: ignore[assignment]


@pytest.fixture
def e2e_workspace_root(tmp_path: Path) -> Path:
    """Create an E2E workspace directory rooted under ``tmp_path``."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root


@pytest.fixture
def e2e_workspace(e2e_workspace_root: Path) -> workspace_builder.NonTrivialWorkspace:
    """Construct a non-trivial Rust workspace fixture."""
    return workspace_builder.create_nontrivial_workspace(e2e_workspace_root)


@pytest.fixture
def e2e_git_repo(e2e_workspace: workspace_builder.NonTrivialWorkspace) -> Path:
    """Initialise a Git repository containing ``e2e_workspace`` and commit it."""
    repo_root = e2e_workspace.root
    git_helpers.git_init(repo_root)
    git_helpers.git_config_user(repo_root)
    git_helpers.git_add_all(repo_root)
    git_helpers.git_commit(repo_root, "Initial commit")
    return repo_root


@pytest.fixture
def e2e_workspace_with_git(
    e2e_workspace: workspace_builder.NonTrivialWorkspace,
    e2e_git_repo: Path,
) -> tuple[workspace_builder.NonTrivialWorkspace, Path]:
    """Return the E2E workspace paired with its Git repository root."""
    return e2e_workspace, e2e_git_repo


@pytest.fixture
def staging_cleanup() -> typ.Callable[[Path], None]:
    """Return a helper that removes the publish staging directory parent."""

    def _cleanup(staging_root: Path) -> None:
        build_root = staging_root.parent
        if build_root.exists():
            git_helpers.rmtree(build_root)

    return _cleanup
