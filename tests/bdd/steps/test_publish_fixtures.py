"""Shared pytest fixtures for publish BDD steps."""

from __future__ import annotations

import pytest

from .test_publish_infrastructure import (
    PreflightTestContext,
    ResponseProvider,
    _PreflightInvocationRecorder,
)


@pytest.fixture
def preflight_overrides() -> dict[tuple[str, ...], ResponseProvider]:
    """Provide per-scenario overrides for publish command invocations."""
    return {}


@pytest.fixture
def preflight_recorder() -> _PreflightInvocationRecorder:
    """Capture arguments passed to mocked pre-flight commands."""
    return _PreflightInvocationRecorder()


@pytest.fixture
def preflight_test_context(
    cmd_mox: object,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    preflight_recorder: _PreflightInvocationRecorder,
) -> PreflightTestContext:
    """Provide a preflight test context combining mock, overrides, and recorder."""
    return PreflightTestContext(
        cmd_mox=cmd_mox,
        overrides=preflight_overrides,
        recorder=preflight_recorder,
    )
