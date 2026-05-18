"""Scenario loader for publish-command behavioural tests.

Registers all scenarios from ``tests/bdd/features/publish.feature`` with
pytest-bdd, making them discoverable by the pytest runner without requiring
individual step-function imports in this file.

Step definitions live in the sibling ``test_publish_given_steps``,
``test_publish_when_steps``, and ``test_publish_then_steps`` modules;
shared step definitions are in ``test_common_steps``.

Feature scenarios covered:
- ``--allow-unpublished-workspace-deps`` accepted in dry-run mode,
- flag rejected when combined with ``--live``,
- in-plan index-lookup failure downgraded to a warning.
"""

from __future__ import annotations

from pytest_bdd import scenarios

from .test_common_steps import _FEATURES_DIR

scenarios(str(_FEATURES_DIR / "publish.feature"))
