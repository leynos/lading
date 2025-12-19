"""Pytest configuration for the lading test-suite."""

from __future__ import annotations

import os
import textwrap
import typing as typ
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
    "tests.e2e.steps.test_e2e_steps",
)


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_workspace_env() -> typ.Iterator[None]:
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
def write_config(tmp_path: Path) -> typ.Callable[[str], Path]:
    """Return a helper that writes ``lading.toml`` into ``tmp_path``."""
    from lading import config as config_module

    def _write(body: str) -> Path:
        config_path = tmp_path / config_module.CONFIG_FILENAME
        config_path.write_text(textwrap.dedent(body).lstrip())
        return config_path

    return _write


@pytest.fixture
def minimal_config(write_config: typ.Callable[[str], Path]) -> Path:
    """Persist a representative configuration file for CLI exercises."""
    return write_config(
        """
        [bump]
        [publish]
        strip_patches = "all"
        """
    )
