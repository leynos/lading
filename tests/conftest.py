"""Pytest configuration for the lading test-suite."""

from __future__ import annotations

import collections.abc as cabc
import os
import textwrap
from pathlib import Path

import pytest

pytest_plugins = (
    "cmd_mox.pytest_plugin",
    "tests.bdd.steps.config_fixtures",
    "tests.bdd.steps.manifest_fixtures",
    "tests.bdd.steps.metadata_fixtures",
    "tests.bdd.steps.test_bump_steps",
    "tests.bdd.steps.test_publish_fixtures",
    "tests.bdd.steps.test_publish_given_steps",
    "tests.bdd.steps.test_publish_when_steps",
    "tests.bdd.steps.test_publish_then_steps",
    "tests.bdd.steps.test_publish_skip_steps",
    "tests.e2e.steps.test_e2e_steps",
)


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root directory.

    Returns
    -------
    Path
        Absolute path to the repository root.

    Examples
    --------
    >>> def test_reads_project(repo_root):  # doctest: +SKIP
    ...     assert (repo_root / "pyproject.toml").exists()
    """
    return Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_staging_leaks(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> cabc.Iterator[None]:
    """Fail the test that leaves a staged workspace behind.

    Each test gets its own temporary directory, so a leaked
    ``lading-publish-*`` tree is attributed to the test that created it rather
    than discovered months later as 214 GB on a shared host (issue #269).

    Both ``tempfile.tempdir`` and ``TMPDIR`` are set, because neither alone
    covers the suite. ``tempfile`` caches the resolved directory on first use,
    so the environment variable has no effect on this process mid-session; and
    the attribute is process-local, so it has no effect on the lading
    subprocesses the command-line scenarios start.

    Yields
    ------
    None
        Control, while the temporary directory is redirected.

    Raises
    ------
    AssertionError
        If the test left a staged workspace behind.
    """
    import tempfile

    staging_area = tmp_path_factory.mktemp("tmpdir")
    monkeypatch.setattr(tempfile, "tempdir", str(staging_area))
    monkeypatch.setenv("TMPDIR", str(staging_area))
    yield
    leaked = sorted(staging_area.glob("lading-publish-*"))
    if leaked:
        message = f"staged workspaces left behind: {leaked}"
        raise AssertionError(message)


@pytest.fixture(autouse=True)
def _restore_workspace_env() -> cabc.Iterator[None]:
    """Ensure tests do not leak ``LADING_WORKSPACE_ROOT`` between runs."""
    from lading.cli import WORKSPACE_ROOT_ENV_VAR

    original = os.environ.get(WORKSPACE_ROOT_ENV_VAR)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(WORKSPACE_ROOT_ENV_VAR, None)
        else:
            os.environ[WORKSPACE_ROOT_ENV_VAR] = original


@pytest.fixture
def write_config(tmp_path: Path) -> cabc.Callable[[str], Path]:
    """Return a helper that writes ``lading.toml`` into ``tmp_path``.

    Parameters
    ----------
    tmp_path : Path
        Per-test temporary directory provided by pytest.

    Returns
    -------
    cabc.Callable[[str], Path]
        Callable that writes the given body and returns the config path.

    Examples
    --------
    >>> def test_writes_config(write_config):  # doctest: +SKIP
    ...     path = write_config("[bump]")
    ...     assert path.read_text().startswith("[bump]")
    """
    from lading import config as config_module

    def _write(body: str) -> Path:
        config_path = tmp_path / config_module.CONFIG_FILENAME
        config_path.write_text(textwrap.dedent(body).lstrip())
        return config_path

    return _write


@pytest.fixture
def minimal_config(write_config: cabc.Callable[[str], Path]) -> Path:
    """Persist a representative configuration file for CLI exercises.

    Parameters
    ----------
    write_config : cabc.Callable[[str], Path]
        Helper fixture that writes a config body and returns its path.

    Returns
    -------
    Path
        Path to the written configuration file.

    Examples
    --------
    >>> def test_loads_minimal(minimal_config):  # doctest: +SKIP
    ...     assert minimal_config.name == "lading.toml"
    """
    return write_config(
        """
        [bump]
        [publish]
        strip_patches = "all"
        """
    )
