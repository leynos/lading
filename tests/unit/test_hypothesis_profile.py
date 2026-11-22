"""Regression tests for the suite-wide Hypothesis deadline profile.

The ``lading`` profile is registered and loaded by ``tests/conftest.py``. These
tests use only Hypothesis's public settings API, so they fail if the profile
stops being registered or loaded without depending on a failing property test
or on wall-clock timing.
"""

import pytest
from hypothesis import settings

#: Captured at import time, after ``tests/conftest.py`` has loaded the profile,
#: to prove that module-level ``settings(...)`` objects inherit the deadline.
_DERIVED = settings(max_examples=17)


def _suite_profile_was_replaced(pytestconfig: pytest.Config) -> bool:
    """Return whether an explicit profile other than ``lading`` was requested."""
    selected = pytestconfig.getoption("--hypothesis-profile", default=None)
    return selected is not None and selected != "lading"


def test_lading_profile_disables_the_deadline() -> None:
    """The registered ``lading`` profile sets no per-example deadline."""
    assert settings.get_profile("lading").deadline is None, (
        "the lading profile must set deadline=None"
    )


def test_active_default_inherits_the_lading_profile(
    pytestconfig: pytest.Config,
) -> None:
    """Loading the profile makes its deadline the suite-wide default."""
    if _suite_profile_was_replaced(pytestconfig):
        pytest.skip("an explicit --hypothesis-profile replaced the lading profile")
    active = settings.default
    assert active is not None, "the lading profile must be loaded"
    assert active.deadline is None, (
        "the active Hypothesis default must set deadline=None"
    )


def test_settings_captured_at_import_inherit_the_profile(
    pytestconfig: pytest.Config,
) -> None:
    """A settings object built after import inherits the suite deadline."""
    if _suite_profile_was_replaced(pytestconfig):
        pytest.skip("an explicit --hypothesis-profile replaced the lading profile")
    assert _DERIVED.deadline is None, (
        "settings objects must inherit deadline=None from the active profile"
    )
    assert _DERIVED.max_examples == 17, (
        "the derived settings object must keep its explicit example count"
    )


def test_lading_profile_changes_only_the_deadline(
    inherited_hypothesis_profile_name: str,
) -> None:
    """The profile leaves the inherited non-deadline settings untouched."""
    profile = settings.get_profile("lading")
    inherited = settings.get_profile(inherited_hypothesis_profile_name)

    assert profile.suppress_health_check == inherited.suppress_health_check, (
        "the lading profile must not change health-check suppression"
    )
    assert profile.max_examples == inherited.max_examples, (
        "the lading profile must not change the default example count"
    )
