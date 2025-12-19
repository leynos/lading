"""Scenario loader for end-to-end pytest-bdd tests."""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/e2e.feature")
