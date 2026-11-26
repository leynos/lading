"""Scenario loader for the ``publish --expect-digests`` behavioural tests.

Registers every scenario in ``tests/bdd/features/publish_expect_digests.feature``
with pytest-bdd. The step definitions live in ``test_publish_digest_steps``.
"""

from pytest_bdd import scenarios

from .test_common_steps import _FEATURES_DIR

scenarios(str(_FEATURES_DIR / "publish_expect_digests.feature"))
