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
    """Provide per-scenario overrides for publish command invocations.

    Returns
    -------
    dict[tuple[str, ...], ResponseProvider]
        An empty mapping for tests to populate with overrides.
    """
    return {}


@pytest.fixture
def preflight_recorder() -> _PreflightInvocationRecorder:
    """Capture arguments passed to mocked pre-flight commands.

    Returns
    -------
    _PreflightInvocationRecorder
        A fresh recorder for capturing pre-flight command invocations.
    """
    return _PreflightInvocationRecorder()


@pytest.fixture
def preflight_test_context(
    cmd_mox: object,
    preflight_overrides: dict[tuple[str, ...], ResponseProvider],
    preflight_recorder: _PreflightInvocationRecorder,
) -> PreflightTestContext:
    """Provide a preflight test context combining mock, overrides, and recorder.

    Parameters
    ----------
    cmd_mox : object
        The cmd-mox controller supplying stubbed command doubles.
    preflight_overrides : dict[tuple[str, ...], ResponseProvider]
        Per-scenario overrides keyed by command tuple.
    preflight_recorder : _PreflightInvocationRecorder
        Recorder capturing the pre-flight command invocations.

    Returns
    -------
    PreflightTestContext
        The bundled mock, overrides, and recorder for a scenario.
    """
    return PreflightTestContext(
        cmd_mox=cmd_mox,
        overrides=preflight_overrides,
        recorder=preflight_recorder,
    )
