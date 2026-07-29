"""Pytest fixtures for end-to-end lading CLI tests."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

import pytest

from tests.e2e.helpers import git_helpers, workspace_builder

if typ.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path


@pytest.fixture
def e2e_workspace_root(tmp_path: Path) -> Path:
    """Create an E2E workspace directory rooted under ``tmp_path``.

    Parameters
    ----------
    tmp_path : Path
        Per-test temporary directory provided by pytest.

    Returns
    -------
    Path
        The created workspace directory.

    Examples
    --------
    Requested by tests needing an empty workspace directory::

        >>> def test_root_exists(e2e_workspace_root):  # doctest: +SKIP
        ...     assert e2e_workspace_root.is_dir()
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root


@pytest.fixture
def e2e_workspace(e2e_workspace_root: Path) -> workspace_builder.NonTrivialWorkspace:
    """Construct a non-trivial Rust workspace fixture.

    Parameters
    ----------
    e2e_workspace_root : Path
        Directory in which the workspace is created.

    Returns
    -------
    workspace_builder.NonTrivialWorkspace
        The constructed workspace fixture.

    Examples
    --------
    Requested by tests needing a populated Cargo workspace::

        >>> def test_has_crates(e2e_workspace):  # doctest: +SKIP
        ...     assert e2e_workspace.crate_names
    """
    return workspace_builder.create_nontrivial_workspace(e2e_workspace_root)


@pytest.fixture
def e2e_git_repo(e2e_workspace: workspace_builder.NonTrivialWorkspace) -> Path:
    """Initialise a Git repository containing ``e2e_workspace`` and commit it.

    Parameters
    ----------
    e2e_workspace : workspace_builder.NonTrivialWorkspace
        The workspace fixture whose root becomes the repository root.

    Returns
    -------
    Path
        The repository root.

    Examples
    --------
    Requested by tests needing a committed Git repository::

        >>> def test_repo_initialised(e2e_git_repo):  # doctest: +SKIP
        ...     assert (e2e_git_repo / ".git").is_dir()
    """
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
    """Return the E2E workspace paired with its Git repository root.

    Parameters
    ----------
    e2e_workspace : workspace_builder.NonTrivialWorkspace
        The constructed workspace fixture.
    e2e_git_repo : Path
        The initialised Git repository root.

    Returns
    -------
    tuple[workspace_builder.NonTrivialWorkspace, Path]
        The workspace fixture and its repository root.

    Examples
    --------
    Requested by steps needing both the workspace and its repository root::

        >>> def test_pairs(e2e_workspace_with_git):  # doctest: +SKIP
        ...     workspace, repo_root = e2e_workspace_with_git
        ...     assert workspace.root == repo_root
    """
    return e2e_workspace, e2e_git_repo


@pytest.fixture
def staging_cleanup() -> cabc.Callable[[Path], None]:
    """Return a helper that removes the publish staging directory parent.

    Returns
    -------
    cabc.Callable[[Path], None]
        A callable that deletes the staging root's parent build directory.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     build_root = Path(tmp) / "build"
    ...     staging_root = build_root / "staging"
    ...     staging_root.mkdir(parents=True)
    ...     cleanup = staging_cleanup.__wrapped__()
    ...     cleanup(staging_root)
    ...     print(build_root.exists())
    False
    """

    def _cleanup(staging_root: Path) -> None:
        build_root = staging_root.parent
        if build_root.exists():
            git_helpers.rmtree(build_root)

    return _cleanup
