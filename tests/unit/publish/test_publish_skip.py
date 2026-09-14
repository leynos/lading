"""Unit tests for the publish pre-flight skip decision."""

from __future__ import annotations

import pytest

from lading.commands.publish_skip import (
    CONFIGURATION_SOURCE,
    SkipPreflightDecision,
    resolve_skip_preflight,
)


def test_absent_override_takes_the_configured_value() -> None:
    """Without a caller override the configuration decides, and says so.

    The source is asserted as well as the value: the publish log names it, so
    an operator reading the log can tell where the decision came from.
    """
    decision = resolve_skip_preflight(None, configured=True)

    assert decision.skip is True
    assert decision.source == CONFIGURATION_SOURCE


def test_absent_override_defaults_to_running_the_checks() -> None:
    """A workspace that configures nothing keeps running the build checks."""
    decision = resolve_skip_preflight(None, configured=False)

    assert decision.skip is False
    assert decision.source == CONFIGURATION_SOURCE


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("requested", [True, False])
def test_override_wins_over_configuration(*, configured: bool, requested: bool) -> None:
    """An explicit override replaces the configured value in both directions.

    The disabling direction is the one that matters for a workspace that sets
    ``[preflight] skip``: ``--no-skip-preflight`` has to bring the checks back.
    """
    override = SkipPreflightDecision(skip=requested, source="the caller")

    decision = resolve_skip_preflight(override, configured=configured)

    assert decision.skip is requested
    assert decision.source == "the caller"
