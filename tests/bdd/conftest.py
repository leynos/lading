"""Test-wide BDD fixture imports to register shared steps."""

from __future__ import annotations

# Import step and fixture modules so their definitions register with pytest-bdd.
from tests.bdd.steps import config_fixtures as _config_fixtures  # noqa: F401
from tests.bdd.steps import manifest_fixtures as _manifest_fixtures  # noqa: F401
from tests.bdd.steps import metadata_fixtures as _metadata_fixtures  # noqa: F401
from tests.bdd.steps import test_publish_fixtures as _publish_fixtures  # noqa: F401
from tests.bdd.steps import test_publish_given_steps as _publish_given  # noqa: F401
from tests.bdd.steps import test_publish_helpers as _publish_helpers  # noqa: F401
from tests.bdd.steps import test_publish_infrastructure as _publish_infra  # noqa: F401
from tests.bdd.steps import test_publish_then_steps as _publish_then  # noqa: F401
from tests.bdd.steps import test_publish_when_steps as _publish_when  # noqa: F401
