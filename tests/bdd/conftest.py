"""Test-wide BDD fixture imports to register shared steps."""

from __future__ import annotations

# Import step and fixture modules so their definitions register with pytest-bdd.
from tests.bdd.steps import config_fixtures as _config_fixtures  # noqa: F401
from tests.bdd.steps import manifest_fixtures as _manifest_fixtures  # noqa: F401
from tests.bdd.steps import metadata_fixtures as _metadata_fixtures  # noqa: F401
