"""Tests for the filesystem-backed Hypothesis settings factory."""

from hypothesis import HealthCheck, settings

from tests.helpers.hypothesis_settings import filesystem_property_settings


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


def test_filesystem_property_settings_inherits_the_active_example_budget() -> None:
    """Omitting the budget keeps the active profile's example count."""
    active = settings.default
    assert active is not None, "Hypothesis should have active default settings"

    configured = filesystem_property_settings()

    assert configured.max_examples == active.max_examples, (
        "an unspecified example budget must be inherited, not replaced"
    )


def test_filesystem_property_settings_inherits_the_active_suppressions() -> None:
    """Omitting suppressions keeps the active profile's health checks."""
    active = settings.default
    assert active is not None, "Hypothesis should have active default settings"

    configured = filesystem_property_settings(max_examples=20)

    assert configured.suppress_health_check == active.suppress_health_check, (
        "an unspecified suppression list must be inherited, not replaced"
    )


def test_filesystem_property_settings_accepts_an_explicit_empty_suppression() -> None:
    """An explicit empty sequence suppresses nothing, whatever the profile says."""
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


def test_filesystem_property_settings_leaves_the_active_defaults_alone() -> None:
    """Creating filesystem settings must not replace the active defaults.

    Hypothesis selects its built-in ``ci`` profile in CI environments, and that
    profile disables deadlines. The active default is therefore whatever
    profile the run selected, not necessarily the library's ``default``
    profile, so this test compares the active settings with themselves rather
    than requiring a particular deadline.
    """
    before = settings.default
    assert before is not None, "Hypothesis should have active default settings"
    deadline_before = before.deadline
    budget_before = before.max_examples
    suppression_before = before.suppress_health_check

    filesystem_property_settings(max_examples=20)

    after = settings.default
    assert after is before, "the factory must not replace the active defaults"
    assert after.deadline == deadline_before, (
        "the factory must not change the active default deadline"
    )
    assert after.max_examples == budget_before, (
        "the factory must not change the active default example budget"
    )
    assert after.suppress_health_check == suppression_before, (
        "the factory must not change the active default suppressions"
    )
