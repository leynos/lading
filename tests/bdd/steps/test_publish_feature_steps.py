"""Scenario loader for publish-specific behavioural tests."""

from __future__ import annotations

from pytest_bdd import scenarios

from .test_common_steps import _FEATURES_DIR

scenarios(str(_FEATURES_DIR / "publish.feature"))
