"""Tests for the filesystem-backed Hypothesis settings factory.

Hypothesis inherits unspecified settings from whichever profile the run
selected, and its built-in profiles differ: ``ci`` disables deadlines and
suppresses ``HealthCheck.too_slow`` while ``default`` does neither. Assertions
that read the live profile therefore prove different things on a developer
machine and in CI.

Most tests here install a probe profile with values that differ from every
built-in one, so the inheritance assertions bind identically under any profile
the caller selected. The ``probe_profile`` fixture restores the caller's
profile afterwards.
"""

import collections.abc as cabc

import pytest
from hypothesis import HealthCheck, settings

from tests.helpers.hypothesis_settings import filesystem_property_settings

_PROBE_PROFILE = "lading-hypothesis-settings-probe"
_PROBE_BUDGET = 37
_PROBE_SUPPRESSIONS = (HealthCheck.too_slow,)


@pytest.fixture
def probe_profile() -> cabc.Iterator[settings]:
    """Install a known profile and restore the caller's profile afterwards.

    Yields
    ------
    hypothesis.settings
        The settings object the probe profile installed.

    """
    previous = settings.get_current_profile_name()
    settings.register_profile(
        _PROBE_PROFILE,
        max_examples=_PROBE_BUDGET,
        suppress_health_check=list(_PROBE_SUPPRESSIONS),
    )
    settings.load_profile(_PROBE_PROFILE)
    try:
        active = settings.default
        assert active is not None, "loading the probe profile must install settings"
        yield active
    finally:
        settings.load_profile(previous)


def test_filesystem_property_settings_disables_the_deadline() -> None:
    """The factory turns off the per-example deadline."""
    configured = filesystem_property_settings(max_examples=20)

    assert configured.deadline is None, (
        "filesystem-backed properties should not be timed against the disk"
    )


def test_filesystem_property_settings_preserves_max_examples() -> None:
    """The caller's example budget is passed through unchanged."""
    assert filesystem_property_settings(max_examples=20).max_examples == 20, (
        "a small example budget should pass through unchanged"
    )
    assert filesystem_property_settings(max_examples=80).max_examples == 80, (
        "a large example budget should pass through unchanged"
    )


def test_filesystem_property_settings_inherits_the_active_example_budget(
    probe_profile: settings,
) -> None:
    """Omitting the budget keeps the active profile's example count."""
    configured = filesystem_property_settings()

    assert configured.max_examples == probe_profile.max_examples == _PROBE_BUDGET, (
        "an unspecified example budget must be inherited, not replaced"
    )


def test_filesystem_property_settings_inherits_the_active_suppressions(
    probe_profile: settings,
) -> None:
    """Omitting suppressions keeps the active profile's health checks."""
    configured = filesystem_property_settings(max_examples=20)

    assert configured.suppress_health_check == probe_profile.suppress_health_check, (
        "an unspecified suppression list must be inherited, not replaced"
    )
    assert configured.suppress_health_check == _PROBE_SUPPRESSIONS, (
        "the inherited suppressions should be the probe profile's, not the default"
    )


def test_filesystem_property_settings_accepts_an_explicit_empty_suppression(
    probe_profile: settings,
) -> None:
    """An explicit empty sequence suppresses nothing, whatever the profile says."""
    assert probe_profile.suppress_health_check, (
        "the probe profile must suppress something for this test to mean anything"
    )

    configured = filesystem_property_settings(
        max_examples=20,
        suppress_health_check=[],
    )

    assert configured.suppress_health_check == (), (
        "an explicit empty suppression must override the active profile"
    )


def test_filesystem_property_settings_passes_suppression_through() -> None:
    """An explicit health-check suppression is preserved exactly."""
    configured = filesystem_property_settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )

    assert configured.suppress_health_check == (HealthCheck.function_scoped_fixture,), (
        "the caller's suppression choice should pass through unchanged"
    )


def test_filesystem_property_settings_leaves_the_active_defaults_alone(
    probe_profile: settings,
) -> None:
    """Creating filesystem settings must not replace or alter the active profile."""
    filesystem_property_settings(max_examples=20)

    during = settings.default
    assert during is probe_profile, "the factory must not replace the active profile"
    assert during.deadline == probe_profile.deadline, (
        "the factory must not change the active default deadline"
    )
    assert during.max_examples == _PROBE_BUDGET, (
        "the factory must not change the active default example budget"
    )
    assert during.suppress_health_check == _PROBE_SUPPRESSIONS, (
        "the factory must not change the active default suppressions"
    )
